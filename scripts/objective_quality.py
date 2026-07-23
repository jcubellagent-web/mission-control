#!/usr/bin/env python3
"""Shared objective-quality gate for Telegram and Control Tower publishing."""
from __future__ import annotations

import re
from difflib import SequenceMatcher


CURRENT_REQUEST_RE = re.compile(r"(?:^|\n)Current user request:\s*(.*?)\s*$", re.S | re.I)
WORD_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*", re.I)
COURTESY_RE = re.compile(
    r"^(?:please\s+)?(?:can|could|would|may)\s+you(?:\s+please)?\s+|"
    r"^(?:please\s+|i\s+want\s+you\s+to\s+|make\s+sure\s+)",
    re.I,
)
CARD_ROW_RE = re.compile(
    r"^(?:[🎯🤖📊⏱️✅⚠️➡️🔐]\s*)?"
    r"(?:objective|model|steps?|eta|complete|what was done|issues|"
    r"appropriate next steps|approval needed|status|progress)\s*(?::|$)",
    re.I,
)
OUTPUT_INSTRUCTION_RE = re.compile(
    r"^(?:return|respond(?:\s+with)?|output(?:\s+format)?|include)\b"
    r".*\b(?:findings?|model|authentication|auth|route|routing|fallback|"
    r"conclusion|format|sections?|status|complete|issues?|next steps?|approval)\b",
    re.I,
)
CONSTRAINT_ONLY_RE = re.compile(
    r"^(?:(?:please\s+)?(?:make|do)\s+no\s+changes|"
    r"(?:please\s+)?no\s+changes|"
    r"(?:please\s+)?(?:do\s+not|don't)\s+(?:make|apply)(?:\s+any)?\s+changes\b|"
    r"(?:please\s+)?(?:do\s+not|don't)\s+(?:change|edit)\b|"
    r"read[- ]only(?:\s+only)?)[.!]?$",
    re.I,
)
INTENT_RE = re.compile(
    r"\b(?:assess|audit|check|evaluate|examine|find|fix|implement|inspect|"
    r"investigate|repair|review|run|test|validate|verify|build|add|remove|update|"
    r"confirm|create|execute|improve|optimize|resolve|sync|synchronize|reconcile|align)\b",
    re.I,
)
LEADING_INTENT_RE = re.compile(
    r"^(?:(?:please\s+)?|(?:can|could|would|may)\s+you(?:\s+please)?\s+)"
    r"(?:assess|audit|check|evaluate|examine|find|fix|implement|inspect|"
    r"investigate|repair|review|run|test|validate|verify|build|add|remove|update|"
    r"confirm|create|execute|improve|optimize|resolve|sync|synchronize|reconcile|align)\b",
    re.I,
)
TRANSPORT_PREFIX_RE = re.compile(
    r"^(?:\[J(?:\|[^\]]+)?\]\s*|TEST\s+ID\s*:\s*\S+\s*)",
    re.I,
)
ACTION_VERBS = (
    "test", "testing", "validate", "validating", "verify", "confirm", "check",
    "fix", "repair", "resolve", "correct", "review", "audit", "inspect",
    "examine", "assess", "look at", "add", "build", "create", "implement",
    "update", "upgrade", "change", "redesign", "optimize", "improve", "sync",
    "synchronize", "reconcile", "align", "run", "execute",
)
ACTIONABLE_RE = re.compile(
    rf"\b(?:(?:please\s+)?(?:can|could|would|may)\s+you(?:\s+please)?\s+|"
    rf"please\s+|i\s+want\s+you\s+to\s+|make\s+sure\s+)?"
    rf"(?:{'|'.join(re.escape(value) for value in ACTION_VERBS)})\b.+$",
    re.I,
)

GENERIC_CONNECTIVITY_REQUESTS = {
    "test",
    "testing",
    "test test",
    "testing testing",
    "ping",
}
GENERIC_CONNECTIVITY_OBJECTIVE = (
    "Confirm the Telegram agent is responsive and completes a simple request"
)


def _request_lines(prompt: str) -> list[str]:
    """Return human request rows with transport and rendered-card rows removed."""
    raw = prompt or ""
    match = CURRENT_REQUEST_RE.search(raw)
    if match:
        raw = match.group(1)
    lines: list[str] = []
    skip_value = False
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        # A transport marker may occupy a row by itself or prefix the actual
        # request. Remove only the marker so a same-line imperative survives.
        while line and TRANSPORT_PREFIX_RE.match(line):
            line = TRANSPORT_PREFIX_RE.sub("", line, count=1).strip()
        if not line:
            continue
        if re.fullmatch(r"(?:🎯\s*)?objective", line, re.I):
            skip_value = True
            continue
        if skip_value:
            skip_value = False
            continue
        if (
            CARD_ROW_RE.match(line)
            or OUTPUT_INSTRUCTION_RE.match(line)
            or line.startswith(("```", "- ", "[media attached:"))
        ):
            continue
        lines.append(line)
    return lines


def request_context_text(prompt: str) -> str:
    """Return safe request context without card rows or output contracts."""
    return " ".join(_request_lines(prompt)).strip()


def current_request_text(prompt: str) -> str:
    """Select the actionable request, excluding transport and output contracts."""
    lines = _request_lines(prompt)

    parts = [
        part.strip(" ,.-")
        for part in re.split(r"(?<=[.!?])\s+|\n+", "\n".join(lines))
        if part.strip()
    ]
    normalized_parts = [
        re.sub(r"^read[- ]only\s+acceptance\s+check\s*:\s*", "", part, flags=re.I).strip()
        for part in parts
    ]
    candidates = [
        part
        for part in normalized_parts
        if part
        and INTENT_RE.search(part)
        and not CONSTRAINT_ONLY_RE.fullmatch(part)
    ]
    if not candidates:
        return " ".join(part for part in normalized_parts if part).strip()
    # Preserve every actionable clause in source order. Choosing only the
    # longest sentence silently drops legitimate compound work such as
    # "Review Inbox ownership. Fix JAIMES Ops routing." Output-contract rows
    # have already been removed above, so joining these clauses is safe.
    selected = "; ".join(dict.fromkeys(candidates))
    has_no_change_constraint = any(
        CONSTRAINT_ONLY_RE.fullmatch(part)
        or re.search(
            r"\b(?:read[- ]only|(?:make|do|no) changes|"
            r"(?:do not|don't) (?:make|apply)(?: any)? changes)\b",
            part,
            re.I,
        )
        for part in normalized_parts
    )
    if (
        has_no_change_constraint
        and not re.search(r"\b(?:read[- ]only|without (?:making )?changes)\b", selected, re.I)
    ):
        selected = f"{selected} read-only"
    return selected


def normalized_words(value: str, *, strip_courtesy: bool = False) -> list[str]:
    text = " ".join((value or "").split()).lower()
    if strip_courtesy:
        text = COURTESY_RE.sub("", text).strip()
    return WORD_RE.findall(text)


def longest_shared_run(left: list[str], right: list[str]) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for left_word in left:
        current = [0]
        for index, right_word in enumerate(right, start=1):
            length = previous[index - 1] + 1 if left_word == right_word else 0
            current.append(length)
            best = max(best, length)
        previous = current
    return best


def objective_is_near_copy(prompt: str, objective: str) -> bool:
    """Reject prompt echoes while allowing short, genuinely summarized labels."""
    request_words = normalized_words(current_request_text(prompt), strip_courtesy=True)
    objective_words = normalized_words(objective)
    if not request_words or not objective_words:
        return True
    if " ".join(request_words) in GENERIC_CONNECTIVITY_REQUESTS:
        # Bare human canaries such as "testing" are actions, not useful
        # operator-facing objectives. Force both primary Telegram runtimes
        # through the same semantic fallback instead of displaying the prompt.
        return True
    ratio = SequenceMatcher(None, request_words, objective_words).ratio()
    shared_run = longest_shared_run(request_words, objective_words)
    exact_after_courtesy = request_words == objective_words
    return bool(
        (exact_after_courtesy and len(objective_words) >= 4)
        or shared_run >= 6
        or (len(objective_words) >= 6 and ratio >= 0.78)
    )


def semantic_reinterpretation(prompt: str) -> str:
    """Turn common imperatives into outcome-oriented objective labels."""
    request = current_request_text(prompt).strip(" .?!")
    # Hermes transport rows and test identifiers can precede the actual ask.
    # Prefer the first actionable imperative so intake does not mistake that
    # metadata (or a short context sentence) for the objective.
    actionable = ACTIONABLE_RE.search(request)
    if actionable:
        request = actionable.group(0)
    request = re.sub(r"^(?:@[a-z0-9_.-]+\s+)+", "", request, flags=re.I)
    request = COURTESY_RE.sub("", request).strip(" .?!")
    if " ".join(normalized_words(request)) in GENERIC_CONNECTIVITY_REQUESTS:
        return GENERIC_CONNECTIVITY_OBJECTIVE
    request_words = set(normalized_words(request))
    if (
        {"model", "routing"}.issubset(request_words)
        and ({"private", "execution"}.issubset(request_words) or "fallback" in request_words)
        and re.match(r"^(?:review|audit|inspect|examine|assess|look at)\b", request, re.I)
    ):
        return "Assess model-routing resilience and private-execution boundaries"
    patterns = (
        (r"^(?:test(?:ing)?|validate|validating|verify|confirm|check|make sure)\s+(.+)$", "Confirm", "meets the intended requirements"),
        (r"^(?:fix|repair|resolve|correct)\s+(.+)$", "Resolve", "and restore expected behavior"),
        (r"^(?:review|audit|inspect|examine|assess|look at)\s+(.+)$", "Assess", "to identify needed changes"),
        (r"^(?:add|build|create|implement)\s+(.+)$", "Deliver", "as a working capability"),
        (r"^(?:update|upgrade|change|redesign|optimize|improve)\s+(.+)$", "Improve", "while preserving expected behavior"),
        (r"^(?:sync|synchronize|reconcile|align)\s+(.+)$", "Unify", "into one consistent state"),
        (r"^(?:run|execute)\s+(.+)$", "Execute", "and report actionable results"),
    )
    for pattern, action, outcome in patterns:
        match = re.match(pattern, request, re.I)
        if not match:
            continue
        target = " ".join(match.group(1).strip(" .?!").split())
        if not target:
            return ""
        target_words = target.split()
        if len(target_words) > 5:
            # This is a deterministic fallback, not a prompt echo. Retain the
            # subject and outcome nouns while breaking long copied word runs.
            target = " ".join([*target_words[:3], *target_words[-2:]])
        allowance = max(12, 80 - len(action) - len(outcome) - 2)
        target = target[:allowance].rstrip(" ,.;:-")
        return f"{action} {target} {outcome}"[:80].rstrip()
    return ""
