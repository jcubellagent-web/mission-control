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
    r"^(?:objective|model|steps?|eta|complete|what was done|issues|"
    r"appropriate next steps|approval needed|status|progress)\s*(?::|$)",
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


def current_request_text(prompt: str) -> str:
    """Keep only the current request and discard transport/card evidence."""
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
            or line.startswith(("```", "[media attached:"))
        ):
            continue
        lines.append(line)
    return " ".join(lines).strip()


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
