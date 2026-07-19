#!/usr/bin/env python3
"""Shared privacy contract for Brain Atlas work display labels.

The ledger keeps exact identifiers and full operational fields.  Brain Atlas
may expose only a short, normalized title that passes this fail-closed filter.
Generator and dashboard sanitizer both import this module so their acceptance
rules cannot drift.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


WORK_LABEL_MAX_LENGTH = 56
SAFE_AGENT_LABELS = frozenset({"JOSHeX", "JOSH 2.0", "JAIMES", "J.A.I.N"})
SAFE_MODEL_FAMILIES = frozenset({"codex", "antigravity", "ollama", "grok"})
MODEL_ID_MAX_LENGTH = 80
SAFE_EXACT_WORK_LABELS = frozenset({"Handle /new"})

_GENERIC_PHASES = frozenset({
    "accepted", "active", "blocked", "cancelled", "complete", "completed",
    "done", "error", "execution", "heartbeat", "in progress", "pending",
    "planned", "planning", "ready", "routed", "running", "started",
    "starting", "terminal", "update", "verifying", "working",
})
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])")
_URL = re.compile(r"(?i)(?:\b[a-z][a-z0-9+.-]{2,}://|\bwww\.)\S+")
_UNIX_PATH = re.compile(r"(?:^|[\s(\[{'\"])/(?:[^\s/]+/)*[^\s/]+")
_HOME_PATH = re.compile(r"(?:^|[\s(\[{'\"])~/(?:\S+)")
_WINDOWS_PATH = re.compile(r"(?i)(?:^|[\s(\[{'\"])[A-Z]:[\\/](?:\S+)")
_HTML = re.compile(r"<[^>]{1,120}>")
_UUID = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b")
_HASH = re.compile(
    r"(?i)(?<![0-9a-f])(?=[0-9a-f]{8,64}(?![0-9a-f]))"
    r"(?=[0-9a-f]*[a-f])[0-9a-f]{8,64}"
)
_STABLE_ID = re.compile(
    r"(?i)\b(?:work|run|event|receipt|edge|thread|session|task|job|request)"
    r"[_:][A-Z0-9][A-Z0-9._:-]{5,}\b"
)
_DASHED_RAW_ID = re.compile(
    r"(?i)\b(?:work|run|event|receipt|edge|thread|session|task|job|request)-"
    r"(?=[A-Z0-9._:-]{12,}\b)(?:[A-Z0-9._:-]*\d[A-Z0-9._:-]*|[A-Z0-9._:-]{20,})\b"
)
_LEGACY_WORK_LABEL = re.compile(r"(?i)^work\s+[a-z0-9_-]{6,}$")
_SECRET_TOKEN = re.compile(
    r"(?i)(?:"
    r"\bsk-(?:proj-|live-|test-)?[A-Z0-9_-]{12,}|"
    r"\b(?:sk|pk)_(?:live|test)_[A-Z0-9]{12,}|"
    r"\bgh[opusr]_[A-Z0-9]{20,}|"
    r"\bxox[baprs]-[A-Z0-9-]{12,}|"
    r"\bglpat-[A-Z0-9_-]{12,}|"
    r"\bnpm_[A-Z0-9]{20,}|"
    r"\bAIza[A-Z0-9_-]{20,}|"
    r"\bAKIA[A-Z0-9]{16}\b|"
    r"\bASIA[A-Z0-9]{16}\b|"
    r"\b\d{6,12}:[A-Z0-9_-]{20,}|"
    r"\beyJ[A-Z0-9_-]{8,}\.[A-Z0-9_-]{8,}\.[A-Z0-9_-]{8,}\b|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r")"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|auth(?:orization)?|"
    r"bearer|cookie|password|passwd|secret|session[ _-]?id)\s*(?:=|:)\s*\S{4,}"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Z0-9._~+/=-]{12,}")
_OPAQUE_TOKEN_CHARS = re.compile(r"^[A-Za-z0-9._~+/=-]+$")
_IP_ADDRESS = re.compile(
    r"(?<!\d)(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?!\d)"
)
_SSN = re.compile(r"(?<!\d)\d{3}[ -]?\d{2}[ -]?\d{4}(?!\d)")
_CARD_LIKE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_LONG_DIGIT_RUN = re.compile(r"(?<!\d)\d{9,19}(?!\d)")
_PHONE_CANDIDATE = re.compile(
    r"(?<!\w)(?:\+?\d|\(\d{2,4}\))[\d(). -]{5,}\d(?!\w)"
)
_LABELED_PHONE = re.compile(
    r"(?i)\b(?:phone|mobile|cell|call|text|sms|tel)\b[^\d+]{0,16}"
    r"(?:\+?\d|\(\d{2,4}\))[\d(). -]{5,}\d"
)
_ACCOUNT_VALUE = re.compile(
    r"(?i)\b(?:account|acct|routing|iban|swift|sort[ -]?code|wallet|"
    r"credit[ -]?card|debit[ -]?card|bank[ -]?account)\b\s*"
    r"(?:(?:number|no\.?|id)?\s*[#:=]\s*[A-Z0-9][A-Z0-9 ._-]{3,}|"
    r"(?:number|no\.?|id)\s+[A-Z0-9][A-Z0-9 ._-]{3,}|"
    r"(?=[A-Z0-9]{4,}\b)(?=[A-Z0-9]*\d)[A-Z0-9]{4,})"
)
_ACCOUNT_LAST_FOUR = re.compile(
    r"(?i)\b(?:account|acct|wallet|credit[ -]?card|debit[ -]?card|card)\b"
    r".{0,16}(?:ending|last|\*{2,}|x{2,})\s*(?:in\s*)?\d{4}\b"
)
_IBAN = re.compile(r"(?i)\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b")
_CURRENCY_AMOUNT = re.compile(
    r"(?i)(?:[$\u20ac\u00a3\u00a5\u20b9]\s*\d|"
    r"\b(?:USD|EUR|GBP|JPY|CAD|AUD|NZD|CHF|BTC|ETH|SOL|USDC|USDT)"
    r"\s*[:=]?\s*\d|"
    r"\b\d[\d,]*(?:\.\d+)?\s*"
    r"(?:USD|EUR|GBP|JPY|CAD|AUD|NZD|CHF|BTC|ETH|SOL|USDC|USDT)\b)"
)
_FINANCIAL_VALUE = re.compile(
    r"(?i)\b(?:balance|salary|income|revenue|spend|cost|budget|payment|"
    r"invoice|portfolio|net[ -]?worth)\b.{0,20}(?:[$\u20ac\u00a3\u00a5\u20b9]|\d)"
)
_DATE_OF_BIRTH = re.compile(
    r"(?i)\b(?:dob|date[ -]?of[ -]?birth|birth[ -]?date)\b.{0,16}"
    r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}"
)
_STREET_ADDRESS = re.compile(
    r"(?i)\b\d{1,6}\s+[A-Z][A-Z0-9 .'-]{1,48}\s+"
    r"(?:street|st|road|rd|avenue|ave|boulevard|blvd|lane|ln|drive|dr|"
    r"court|ct|place|pl|parkway|pkwy)\b"
)
_LABELED_PII = re.compile(
    r"(?i)\b(?:full[ -]?name|customer[ -]?name|client[ -]?name|legal[ -]?name|"
    r"home[ -]?address|mailing[ -]?address|passport|driver(?:'s)?[ -]?license|"
    r"medical[ -]?record|tax[ -]?id)\b\s*(?:=|:|#)\s*\S.{2,}"
)

_MODEL_ID_PATTERNS = {
    "codex": re.compile(
        r"(?:(?:openai-codex|openai|codex)/)?"
        r"(?:gpt-[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?|"
        r"o[1-9](?:[a-z0-9.-]{0,63}[a-z0-9])?|"
        r"codex-[a-z0-9](?:[a-z0-9.-]{0,60}[a-z0-9])?)"
    ),
    "antigravity": re.compile(
        r"(?:(?:google|antigravity)/)?"
        r"(?:gemini|gemma)-[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?"
    ),
    "ollama": re.compile(
        r"(?:ollama/)?(?:qwen|llama|gemma|mistral|mixtral|phi|deepseek|codellama|"
        r"nomic|bge|snowflake-arctic|command-r|glm)[a-z0-9.-]*"
        r"(?::[a-z0-9](?:[a-z0-9.-]{0,30}[a-z0-9])?)?"
    ),
    "grok": re.compile(
        r"(?:(?:xai|grok)/)?grok-[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?"
    ),
}


def normalize_display_text(value: Any) -> str | None:
    """Return canonical display text, rejecting control/format characters."""

    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        return None
    collapsed = " ".join(normalized.split())
    return collapsed or None


def _contains_opaque_token(text: str) -> bool:
    for raw_token in text.split():
        token = raw_token.strip(".,;:!?()[]{}<>\"'")
        if len(token) < 24 or not _OPAQUE_TOKEN_CHARS.fullmatch(token):
            continue
        if len(token) >= 32:
            return True
        if any(character.isdigit() for character in token):
            return True
        if sum(character in "._~+/=-" for character in token) >= 2:
            return True
    return False


def _contains_phone_like(text: str) -> bool:
    for match in _PHONE_CANDIDATE.finditer(text):
        digit_count = sum(character.isdigit() for character in match.group(0))
        if 10 <= digit_count <= 19:
            return True
    if _LABELED_PHONE.search(text):
        return True
    return False


def _contains_private_material(text: str, *, include_opaque: bool = True) -> bool:
    return bool(
        _EMAIL.search(text)
        or _URL.search(text)
        or _UNIX_PATH.search(text)
        or _HOME_PATH.search(text)
        or _WINDOWS_PATH.search(text)
        or _HTML.search(text)
        or _UUID.search(text)
        or _HASH.search(text)
        or _STABLE_ID.search(text)
        or _DASHED_RAW_ID.search(text)
        or _LEGACY_WORK_LABEL.fullmatch(text)
        or _SECRET_TOKEN.search(text)
        or _CREDENTIAL_ASSIGNMENT.search(text)
        or _BEARER_TOKEN.search(text)
        or _IP_ADDRESS.search(text)
        or _SSN.search(text)
        or _CARD_LIKE.search(text)
        or _LONG_DIGIT_RUN.search(text)
        or _contains_phone_like(text)
        or _ACCOUNT_VALUE.search(text)
        or _ACCOUNT_LAST_FOUR.search(text)
        or _IBAN.search(text)
        or _CURRENCY_AMOUNT.search(text)
        or _FINANCIAL_VALUE.search(text)
        or _DATE_OF_BIRTH.search(text)
        or _STREET_ADDRESS.search(text)
        or _LABELED_PII.search(text)
        or (include_opaque and _contains_opaque_token(text))
    )


def _truncate_at_word_boundary(text: str) -> str | None:
    if len(text) <= WORK_LABEL_MAX_LENGTH:
        return text
    prefix = text[: WORK_LABEL_MAX_LENGTH - 3].rstrip()
    boundary = prefix.rfind(" ")
    if boundary < 16:
        return None
    return f"{prefix[:boundary].rstrip()}..."


def safe_label_candidate(value: Any) -> str | None:
    """Normalize, screen, and cap one proposed work title."""

    normalized = normalize_display_text(value)
    if normalized is None or _contains_private_material(normalized):
        return None
    capped = _truncate_at_word_boundary(normalized)
    if capped is None or _contains_private_material(capped):
        return None
    return capped


def safe_work_label(objective: Any, phase: Any, agent_label: Any) -> str:
    """Choose objective, then a meaningful phase, then a safe agent fallback."""

    normalized_objective = normalize_display_text(objective)
    objective_label = (
        normalized_objective
        if normalized_objective in SAFE_EXACT_WORK_LABELS
        else safe_label_candidate(objective)
    )
    if objective_label is not None:
        return objective_label

    phase_label = safe_label_candidate(phase)
    if phase_label is not None and phase_label.casefold() not in _GENERIC_PHASES:
        return phase_label

    safe_agent = (
        agent_label
        if isinstance(agent_label, str) and agent_label in SAFE_AGENT_LABELS
        else "Agent"
    )
    return f"{safe_agent} task"


def work_label_is_safe(value: Any) -> bool:
    """Validate a serialized work label at the dashboard trust boundary."""

    normalized = normalize_display_text(value)
    return bool(
        isinstance(value, str)
        and normalized == value
        and 1 <= len(value) <= WORK_LABEL_MAX_LENGTH
        and (
            value in SAFE_EXACT_WORK_LABELS
            or safe_label_candidate(value) == value
        )
    )


def safe_model_route_candidate(family: Any, model_id: Any) -> tuple[str, str] | None:
    """Accept only known, display-safe model identifiers for one route family."""

    if not isinstance(family, str) or not isinstance(model_id, str):
        return None
    if family not in SAFE_MODEL_FAMILIES:
        return None
    if not 1 <= len(model_id) <= MODEL_ID_MAX_LENGTH:
        return None
    if normalize_display_text(family) != family or normalize_display_text(model_id) != model_id:
        return None
    if not model_id.isascii() or model_id != model_id.lower():
        return None
    if _contains_private_material(model_id, include_opaque=False):
        return None
    if any(part.isdigit() and len(part) > 4 for part in re.split(r"[.:-]", model_id)):
        return None
    pattern = _MODEL_ID_PATTERNS.get(family)
    if pattern is None or pattern.fullmatch(model_id) is None:
        return None
    return family, model_id


def model_route_node_is_safe(
    family: Any,
    model_id: Any,
    label: Any,
) -> bool:
    """Validate the complete serialized model-node display contract."""

    route = safe_model_route_candidate(family, model_id)
    return bool(route is not None and label == f"{route[0]}/{route[1]}")
