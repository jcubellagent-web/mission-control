#!/usr/bin/env python3
"""Analyze collected public X posts with deterministic, local-only heuristics.

The analyzer intentionally does not fetch URLs, inspect a browser, infer source
credibility, or make trading recommendations. It accepts the JSON emitted by
``collect_search.mjs`` (one collection, a list of collections, or an object with
``collections``) and emits a transparent coverage, sentiment, and manipulation-
indicator summary.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit


VERSION = 1
MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_POSTS = 5_000
MAX_TEXT_LENGTH = 10_000
MAX_URL_LENGTH = 2_048

X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
STATUS_PATH = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d+)(?:/.*)?$")
INTERNAL_STATUS_PATH = re.compile(r"^/i/web/status/(\d+)(?:/.*)?$")
HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
SAFE_ERROR_CODE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
URL_IN_TEXT = re.compile(r"(?i)\b(?:https?://|www\.|t\.co/)[^\s<>()]+")
MENTION = re.compile(r"(?<!\w)@[A-Za-z0-9_]{1,15}\b")
WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
CASHTAG = re.compile(r"(?<!\w)\$[A-Za-z][A-Za-z0-9._-]{0,14}\b")
HASHTAG = re.compile(r"(?<!\w)#[^\W_]+", re.UNICODE)
EVM_ADDRESS = re.compile(r"(?<![A-Fa-f0-9])0x[A-Fa-f0-9]{40}(?![A-Fa-f0-9])")

BULLISH_TERMS = {
    "accumulate",
    "accumulation",
    "adoption",
    "breakout",
    "bullish",
    "inflow",
    "inflows",
    "listed",
    "listing",
    "momentum",
    "outperform",
    "partnership",
    "rally",
    "recovery",
    "surge",
    "undervalued",
    "upgrade",
    "upside",
}

BEARISH_TERMS = {
    "bearish",
    "breakdown",
    "crash",
    "delist",
    "delisted",
    "dilution",
    "downside",
    "dump",
    "dumping",
    "exploit",
    "exploited",
    "freeze",
    "frozen",
    "hack",
    "hacked",
    "honeypot",
    "liquidation",
    "outflow",
    "rug",
    "rugpull",
    "scam",
    "selloff",
    "vulnerable",
}

BULLISH_PHRASES = {"all time high", "new high", "strong buy"}
BEARISH_PHRASES = {"dead cat bounce", "exit liquidity", "strong sell"}
NEGATORS = {"ain't", "isn't", "never", "no", "not", "wasn't", "without", "won't"}

MANIPULATION_PATTERNS = {
    "engagement_bait": re.compile(
        r"(?i)\b(?:like\s*(?:,|and)?\s*(?:repost|retweet)|"
        r"(?:repost|retweet)\s*(?:,|and)?\s*(?:follow|like)|"
        r"follow\s*(?:,|and)?\s*(?:tag|repost|retweet)|"
        r"tag\s+\d+\s+(?:friends?|people)|comment\s+(?:below|your))\b"
    ),
    "giveaway_or_airdrop_promotion": re.compile(
        r"(?i)\b(?:airdrop|allowlist|free\s+tokens?|giveaway|presale|whitelist)\b"
    ),
    "guaranteed_return_language": re.compile(
        r"(?i)(?:\b(?:guaranteed|risk[- ]?free|sure\s+thing|can't\s+lose|cannot\s+lose|"
        r"easy\s+money)\b|(?<!\w)\d{2,5}x(?!\w))"
    ),
    "urgency_or_fomo": re.compile(
        r"(?i)\b(?:act\s+now|buy\s+before|don't\s+miss|last\s+chance|"
        r"before\s+it's\s+too\s+late|join\s+now|moonshot|send\s+it)\b"
    ),
    "referral_or_affiliate_link": re.compile(
        r"(?i)(?:[?&](?:aff|affiliate|invite|promo|ref|referral)="
        r"|\b(?:affiliate|invite|promo|referral)\s+(?:code|link)\b)"
    ),
}

INDICATOR_WEIGHTS = {
    "repeated_cross_author_text": 3,
    "guaranteed_return_language": 3,
    "engagement_bait": 2,
    "referral_or_affiliate_link": 2,
    "giveaway_or_airdrop_promotion": 1,
    "urgency_or_fomo": 1,
    "excessive_tags": 1,
    "exaggerated_formatting": 1,
    "promotional_ticker_without_identifier": 1,
}


class InputError(ValueError):
    """Raised for bounded, user-correctable input failures."""


@dataclass(frozen=True)
class Post:
    input_index: int
    status_url: str
    author_handle: str
    timestamp: str
    text: str


@dataclass
class TextGroup:
    representative: Post
    member_count: int
    authors: set[str]
    exact_duplicates: int
    near_duplicates: int
    tokens: tuple[str, ...]
    shingles: frozenset[tuple[str, ...]]


def compact_error(code: str, detail: str) -> str:
    return json.dumps({"ok": False, "error": code, "detail": detail}, sort_keys=True)


def read_input(path: str) -> object:
    try:
        if path == "-":
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        else:
            with Path(path).open("rb") as stream:
                raw = stream.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise InputError("Could not read the input file.") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise InputError(f"Input exceeds the {MAX_INPUT_BYTES}-byte limit.")
    if not raw.strip():
        raise InputError("Input is empty.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError("Input must be valid UTF-8 JSON.") from exc


def collection_failure_code(collection: dict) -> str:
    value = collection.get("error")
    if isinstance(value, str):
        value = value.strip().lower()
        if SAFE_ERROR_CODE.fullmatch(value):
            return value
    return "unspecified"


def reported_post_count(collection: dict) -> int:
    coverage = collection.get("coverage")
    if not isinstance(coverage, dict):
        return 0
    value = coverage.get("postCount")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def extract_rows(payload: object) -> tuple[list[object], dict]:
    """Return raw post rows and collection-level coverage metadata."""

    if isinstance(payload, dict) and "collections" in payload:
        collections = payload["collections"]
        if not isinstance(collections, list):
            raise InputError("The collections field must be a JSON array.")
    elif isinstance(payload, list):
        if all(isinstance(item, dict) and ("posts" in item or "ok" in item) for item in payload):
            collections = payload
        else:
            collections = [{"ok": True, "posts": payload}]
    elif isinstance(payload, dict) and ("posts" in payload or "ok" in payload):
        collections = [payload]
    elif isinstance(payload, dict) and ("text" in payload or "statusUrl" in payload):
        collections = [{"ok": True, "posts": [payload]}]
    else:
        raise InputError(
            "Expected a collector object, a list of collector objects, or a JSON array of posts."
        )

    rows: list[object] = []
    failure_codes: Counter[str] = Counter()
    reported = 0
    summary_only_collections = 0
    for collection in collections:
        if not isinstance(collection, dict):
            raise InputError("Every collection must be a JSON object.")
        reported += reported_post_count(collection)
        if collection.get("ok") is False:
            failure_codes[collection_failure_code(collection)] += 1
            continue
        posts = collection.get("posts")
        if posts is None:
            if collection.get("ok") is True:
                summary_only_collections += 1
                continue
            raise InputError("A successful collection must contain a posts array.")
        if not isinstance(posts, list):
            raise InputError("Every posts field must be a JSON array.")
        if len(rows) + len(posts) > MAX_POSTS:
            raise InputError(f"Input contains more than {MAX_POSTS} posts.")
        rows.extend(posts)

    return rows, {
        "inputCollectionCount": len(collections),
        "collectionFailureCount": sum(failure_codes.values()),
        "collectionFailureCodes": dict(sorted(failure_codes.items())),
        "summaryOnlyCollectionCount": summary_only_collections,
        "reportedPostCount": reported,
    }


def bounded_string(value: object, field: str, maximum: int, *, optional: bool = True) -> str:
    if value is None and optional:
        return ""
    if not isinstance(value, str):
        raise InputError(f"Post field {field} must be a string.")
    if len(value) > maximum:
        raise InputError(f"Post field {field} exceeds its length limit.")
    return value.strip()


def canonical_status_url(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in X_HOSTS:
        return ""
    match = STATUS_PATH.fullmatch(parsed.path)
    if match:
        handle, status_id = match.groups()
        return f"https://x.com/{handle}/status/{status_id}"
    match = INTERNAL_STATUS_PATH.fullmatch(parsed.path)
    if match:
        return f"https://x.com/i/web/status/{match.group(1)}"
    return ""


def normalized_handle(value: str) -> str:
    value = value.lstrip("@").strip()
    if not HANDLE.fullmatch(value):
        return ""
    return f"@{value.lower()}"


def normalized_timestamp(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        return ""
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_posts(rows: list[object]) -> tuple[list[Post], dict]:
    posts: list[Post] = []
    rejected = 0
    invalid_urls = 0
    invalid_handles = 0
    invalid_timestamps = 0
    textless = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            rejected += 1
            continue
        try:
            raw_url = bounded_string(row.get("statusUrl"), "statusUrl", MAX_URL_LENGTH)
            raw_handle = bounded_string(row.get("authorHandle"), "authorHandle", 100)
            raw_timestamp = bounded_string(row.get("timestamp"), "timestamp", 100)
            text = bounded_string(row.get("text"), "text", MAX_TEXT_LENGTH)
        except InputError:
            rejected += 1
            continue

        status_url = canonical_status_url(raw_url)
        author_handle = normalized_handle(raw_handle)
        timestamp = normalized_timestamp(raw_timestamp)
        if raw_url and not status_url:
            invalid_urls += 1
        if raw_handle and not author_handle:
            invalid_handles += 1
        if raw_timestamp and not timestamp:
            invalid_timestamps += 1
        if not text:
            textless += 1
        if not text and not status_url:
            rejected += 1
            continue
        posts.append(Post(index, status_url, author_handle, timestamp, text))

    return posts, {
        "rejectedPostCount": rejected,
        "invalidStatusUrlCount": invalid_urls,
        "invalidAuthorHandleCount": invalid_handles,
        "invalidTimestampCount": invalid_timestamps,
        "textlessPostCount": textless,
    }


def post_quality(post: Post) -> tuple[int, int, int, int]:
    return (len(post.text), int(bool(post.author_handle)), int(bool(post.timestamp)), -post.input_index)


def deduplicate_status_urls(posts: list[Post]) -> tuple[list[Post], int]:
    by_url: dict[str, Post] = {}
    without_url: list[Post] = []
    duplicates = 0
    for post in posts:
        if not post.status_url:
            without_url.append(post)
            continue
        existing = by_url.get(post.status_url)
        if existing is None:
            by_url[post.status_url] = post
        else:
            duplicates += 1
            if post_quality(post) > post_quality(existing):
                by_url[post.status_url] = post
    result = list(by_url.values()) + without_url
    result.sort(key=lambda post: (post.timestamp, post.status_url, post.author_handle, post.input_index))
    return result, duplicates


def exact_text_key(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(value.split())


def similarity_tokens(text: str) -> tuple[str, ...]:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = URL_IN_TEXT.sub(" urltoken ", value)
    value = MENTION.sub(" handletoken ", value)
    return tuple(WORD.findall(value)[:400])


def token_shingles(tokens: tuple[str, ...], size: int = 3) -> frozenset[tuple[str, ...]]:
    if len(tokens) < size:
        return frozenset()
    return frozenset(tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1))


def near_similarity(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[bool, float, float]:
    if min(len(left), len(right)) < 6:
        return False, 0.0, 0.0
    left_text = " ".join(left)
    right_text = " ".join(right)
    if min(len(left_text), len(right_text)) < 40:
        return False, 0.0, 0.0
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    jaccard = len(left_set & right_set) / len(union) if union else 0.0
    if jaccard < 0.82:
        return False, jaccard, 0.0
    sequence = SequenceMatcher(None, left_text, right_text, autojunk=False).ratio()
    return sequence >= 0.90, jaccard, sequence


def group_duplicate_text(posts: list[Post]) -> tuple[list[TextGroup], dict]:
    groups: list[TextGroup] = []
    exact_index: dict[str, int] = {}
    shingle_index: dict[tuple[str, ...], set[int]] = defaultdict(set)
    exact_duplicates = 0
    near_duplicates = 0

    for post in posts:
        if not post.text:
            groups.append(TextGroup(post, 1, {post.author_handle} - {""}, 0, 0, (), frozenset()))
            continue
        exact_key = exact_text_key(post.text)
        tokens = similarity_tokens(post.text)
        shingles = token_shingles(tokens)
        group_index = exact_index.get(exact_key)
        duplicate_kind = "exact" if group_index is not None else ""

        if group_index is None and shingles:
            overlap_counts: Counter[int] = Counter()
            for shingle in shingles:
                overlap_counts.update(shingle_index.get(shingle, ()))
            candidates = sorted(overlap_counts, key=lambda item: (-overlap_counts[item], item))[:200]
            best: tuple[float, float, int] | None = None
            for candidate in candidates:
                matched, jaccard, sequence = near_similarity(tokens, groups[candidate].tokens)
                if not matched:
                    continue
                score = (sequence, jaccard, -candidate)
                if best is None or score > best:
                    best = score
                    group_index = candidate
            if group_index is not None:
                duplicate_kind = "near"

        if group_index is None:
            group_index = len(groups)
            groups.append(
                TextGroup(
                    representative=post,
                    member_count=1,
                    authors={post.author_handle} - {""},
                    exact_duplicates=0,
                    near_duplicates=0,
                    tokens=tokens,
                    shingles=shingles,
                )
            )
            for shingle in shingles:
                shingle_index[shingle].add(group_index)
        else:
            group = groups[group_index]
            group.member_count += 1
            if post.author_handle:
                group.authors.add(post.author_handle)
            if duplicate_kind == "exact":
                exact_duplicates += 1
                group.exact_duplicates += 1
            else:
                near_duplicates += 1
                group.near_duplicates += 1
            if post_quality(post) > post_quality(group.representative):
                group.representative = post
                group.tokens = tokens
                group.shingles = shingles
        exact_index[exact_key] = group_index

    duplicate_groups = [group for group in groups if group.member_count > 1]
    cross_author_groups = [group for group in duplicate_groups if len(group.authors) > 1]
    return groups, {
        "exactTextDuplicateCount": exact_duplicates,
        "nearTextDuplicateCount": near_duplicates,
        "duplicateTextClusterCount": len(duplicate_groups),
        "crossAuthorDuplicateClusterCount": len(cross_author_groups),
    }


def phrase_hits(value: str, phrases: set[str]) -> list[str]:
    return sorted(phrase for phrase in phrases if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", value))


def sentiment_for(text: str) -> dict:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = WORD.findall(normalized)
    bullish = set(phrase_hits(normalized, BULLISH_PHRASES))
    bearish = set(phrase_hits(normalized, BEARISH_PHRASES))
    for index, token in enumerate(tokens):
        if token not in BULLISH_TERMS and token not in BEARISH_TERMS:
            continue
        negated = any(value in NEGATORS for value in tokens[max(0, index - 3) : index])
        if token in BULLISH_TERMS:
            (bearish if negated else bullish).add(("not " if negated else "") + token)
        else:
            (bullish if negated else bearish).add(("not " if negated else "") + token)
    score = len(bullish) - len(bearish)
    label = "bullish" if score > 0 else "bearish" if score < 0 else "neutral_or_balanced"
    return {
        "label": label,
        "score": score,
        "bullishTerms": sorted(bullish),
        "bearishTerms": sorted(bearish),
    }


def manipulation_for(text: str, group: TextGroup) -> list[str]:
    indicators = [name for name, pattern in MANIPULATION_PATTERNS.items() if pattern.search(text)]
    tag_count = len(CASHTAG.findall(text)) + len(HASHTAG.findall(text))
    if tag_count >= 5:
        indicators.append("excessive_tags")
    letters = [character for character in text if character.isalpha()]
    upper_share = sum(character.isupper() for character in letters) / len(letters) if letters else 0.0
    if (len(letters) >= 20 and upper_share >= 0.70) or "!!!" in text or "???" in text:
        indicators.append("exaggerated_formatting")
    promotional = any(
        name in indicators
        for name in (
            "engagement_bait",
            "giveaway_or_airdrop_promotion",
            "guaranteed_return_language",
            "urgency_or_fomo",
        )
    )
    has_chain_context = bool(EVM_ADDRESS.search(text)) or bool(
        re.search(r"(?i)\b(?:arbitrum|avalanche|base|bitcoin|bnb|ethereum|mainnet|polygon|solana)\b", text)
    )
    if promotional and CASHTAG.search(text) and not has_chain_context:
        indicators.append("promotional_ticker_without_identifier")
    if group.member_count > 1 and len(group.authors) > 1:
        indicators.append("repeated_cross_author_text")
    return sorted(set(indicators))


def evidence_row(post: Post, sentiment: dict, indicators: list[str]) -> dict:
    result = {
        "sentiment": sentiment["label"],
        "bullishTerms": sentiment["bullishTerms"],
        "bearishTerms": sentiment["bearishTerms"],
        "manipulationIndicators": indicators,
    }
    if post.status_url:
        result["statusUrl"] = post.status_url
    if post.author_handle:
        result["authorHandle"] = post.author_handle
    if post.timestamp:
        result["timestamp"] = post.timestamp
    return result


def sentiment_summary(per_post: list[tuple[Post, dict, list[str]]]) -> dict:
    post_votes = Counter(item[1]["label"] for item in per_post)
    author_scores: dict[str, list[int]] = defaultdict(list)
    for index, (post, sentiment, _) in enumerate(per_post):
        author_key = post.author_handle or f"unknown-{index}"
        author_scores[author_key].append(sentiment["score"])
    author_votes: Counter[str] = Counter()
    for scores in author_scores.values():
        total = sum(scores)
        if total > 0:
            author_votes["bullish"] += 1
        elif total < 0:
            author_votes["bearish"] += 1
        elif any(score != 0 for score in scores):
            author_votes["mixed"] += 1
        else:
            author_votes["neutral"] += 1

    directional = author_votes["bullish"] + author_votes["bearish"]
    if directional < 3:
        label = "unclear"
        strength = "insufficient_directional_coverage"
    else:
        bullish_share = author_votes["bullish"] / directional
        bearish_share = author_votes["bearish"] / directional
        if bullish_share >= 0.70:
            label = "bullish"
        elif bearish_share >= 0.70:
            label = "bearish"
        else:
            label = "mixed"
        skew = abs(author_votes["bullish"] - author_votes["bearish"]) / directional
        if directional >= 10 and skew >= 0.75:
            strength = "strong"
        elif directional >= 5 and skew >= 0.50:
            strength = "moderate"
        else:
            strength = "weak"

    return {
        "label": label,
        "strength": strength,
        "basis": "One capped directional vote per author after URL and near-text deduplication.",
        "authorVotes": {
            "bullish": author_votes["bullish"],
            "bearish": author_votes["bearish"],
            "mixed": author_votes["mixed"],
            "neutral": author_votes["neutral"],
            "directional": directional,
        },
        "postVotes": {
            "bullish": post_votes["bullish"],
            "bearish": post_votes["bearish"],
            "neutralOrBalanced": post_votes["neutral_or_balanced"],
        },
    }


def manipulation_summary(
    groups: list[TextGroup], per_post: list[tuple[Post, dict, list[str]]]
) -> dict:
    counts: Counter[str] = Counter()
    affected = 0
    high_severity_posts = 0
    for _, _, indicators in per_post:
        counts.update(indicators)
        if indicators:
            affected += 1
        if any(INDICATOR_WEIGHTS[name] >= 3 for name in indicators):
            high_severity_posts += 1
    analyzed = len(per_post)
    affected_share = affected / analyzed if analyzed else 0.0
    cross_author = counts["repeated_cross_author_text"]
    if not analyzed:
        risk = "unclear"
    elif (cross_author and affected_share >= 0.15) or high_severity_posts >= 2 or affected_share >= 0.50:
        risk = "high"
    elif high_severity_posts or affected_share >= 0.20:
        risk = "medium"
    else:
        risk = "low"

    clusters = []
    for group in groups:
        if group.member_count <= 1:
            continue
        cluster = {
            "memberCount": group.member_count,
            "uniqueAuthorCount": len(group.authors),
            "exactDuplicateCount": group.exact_duplicates,
            "nearDuplicateCount": group.near_duplicates,
        }
        if group.representative.status_url:
            cluster["representativeStatusUrl"] = group.representative.status_url
        clusters.append(cluster)
    clusters.sort(
        key=lambda item: (
            -item["memberCount"],
            -item["uniqueAuthorCount"],
            item.get("representativeStatusUrl", ""),
        )
    )
    return {
        "risk": risk,
        "affectedPostCount": affected,
        "affectedShare": round(affected_share, 4),
        "indicatorCounts": dict(sorted(counts.items())),
        "duplicateTextClusters": clusters[:20],
        "interpretation": (
            "Indicators are pattern matches, not proof of coordination, intent, authenticity, or fraud."
        ),
    }


def confidence_summary(coverage: dict, sentiment: dict, manipulation: dict) -> dict:
    directional = sentiment["authorVotes"]["directional"]
    enough_coverage = (
        coverage["analyzedPostCount"] >= 10
        and coverage["analyzedUniqueAuthorCount"] >= 5
        and directional >= 5
    )
    label = "medium" if enough_coverage and manipulation["risk"] != "high" else "low"
    reasons = []
    if coverage["analyzedPostCount"] < 10:
        reasons.append("fewer than 10 deduplicated text posts")
    if coverage["analyzedUniqueAuthorCount"] < 5:
        reasons.append("fewer than 5 identified authors")
    if directional < 5:
        reasons.append("fewer than 5 directional author votes")
    if manipulation["risk"] == "high":
        reasons.append("high promotional/manipulation-indicator risk")
    reasons.append("no source-tier, factual, primary-source, or onchain verification was performed")
    return {"label": label, "reasons": reasons}


def analyze(payload: object, max_evidence: int) -> dict:
    rows, collection_coverage = extract_rows(payload)
    validated, validation_coverage = validate_posts(rows)
    status_deduped, duplicate_urls = deduplicate_status_urls(validated)
    groups, text_coverage = group_duplicate_text(status_deduped)
    representatives = [group.representative for group in groups if group.representative.text]

    group_by_identity = {id(group.representative): group for group in groups}
    per_post: list[tuple[Post, dict, list[str]]] = []
    for post in representatives:
        group = group_by_identity[id(post)]
        sentiment = sentiment_for(post.text)
        indicators = manipulation_for(post.text, group)
        per_post.append((post, sentiment, indicators))

    author_handles = {post.author_handle for post in validated if post.author_handle}
    analyzed_authors = {post.author_handle for post in representatives if post.author_handle}
    timestamps = sorted(post.timestamp for post in validated if post.timestamp)
    coverage = {
        **collection_coverage,
        "inputPostCount": len(rows),
        **validation_coverage,
        "structurallyValidPostCount": len(validated),
        "inputUniqueAuthorCount": len(author_handles),
        "duplicateStatusUrlCount": duplicate_urls,
        "postCountAfterStatusUrlDeduplication": len(status_deduped),
        **text_coverage,
        "analyzedPostCount": len(representatives),
        "analyzedUniqueAuthorCount": len(analyzed_authors),
        "earliestTimestampUtc": timestamps[0] if timestamps else None,
        "latestTimestampUtc": timestamps[-1] if timestamps else None,
    }
    coverage["partialCoverage"] = bool(
        coverage["collectionFailureCount"]
        or coverage["summaryOnlyCollectionCount"]
        or coverage["rejectedPostCount"]
        or coverage["invalidStatusUrlCount"]
        or coverage["invalidTimestampCount"]
    )

    sentiment = sentiment_summary(per_post)
    manipulation = manipulation_summary(groups, per_post)
    confidence = confidence_summary(coverage, sentiment, manipulation)

    ordered_evidence = sorted(
        per_post,
        key=lambda item: (item[0].timestamp, item[0].status_url, item[0].author_handle),
        reverse=True,
    )
    evidence = {
        "bullish": [
            evidence_row(*item) for item in ordered_evidence if item[1]["label"] == "bullish"
        ][:max_evidence],
        "bearish": [
            evidence_row(*item) for item in ordered_evidence if item[1]["label"] == "bearish"
        ][:max_evidence],
        "flagged": [evidence_row(*item) for item in ordered_evidence if item[2]][:max_evidence],
    }

    return {
        "ok": True,
        "version": VERSION,
        "coverage": coverage,
        "sentiment": sentiment,
        "confidence": confidence,
        "manipulationAssessment": manipulation,
        "representativeEvidence": evidence,
        "methodology": {
            "deduplication": (
                "Canonical X status URL first; then normalized exact text and near-identical text "
                "with token Jaccard >= 0.82 and sequence similarity >= 0.90."
            ),
            "sentiment": (
                "Fixed, unweighted bullish/bearish term lexicons with local negation handling; "
                "post scores are capped to one directional vote per author."
            ),
            "engagementUsed": False,
            "sourceCredibilityInferred": False,
            "networkOrBrowserAccess": False,
        },
        "decisionUse": (
            "Research triage only. This output is not financial advice, a recommendation to trade, "
            "or factual corroboration; verify material claims with primary and onchain sources."
        ),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--input",
        default="-",
        metavar="PATH",
        help="collector JSON file; omit or use - to read stdin",
    )
    result.add_argument("--max-evidence", type=int, choices=range(0, 21), default=5)
    result.add_argument("--compact", action="store_true", help="emit one-line JSON")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        payload = read_input(args.input)
        result = analyze(payload, args.max_evidence)
    except InputError as exc:
        sys.stderr.write(compact_error("invalid-input", str(exc)) + "\n")
        return 2
    indent = None if args.compact else 2
    sys.stdout.write(json.dumps(result, indent=indent, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
