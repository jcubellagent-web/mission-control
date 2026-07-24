#!/usr/bin/env python3
"""Shared mobile-safe formatter for Telegram terminal summaries."""
from __future__ import annotations

import html
import unicodedata
from collections.abc import Sequence


FINAL_WRAP_WIDTH = 38


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\t", " ").split())


def _cell_width(value: str) -> int:
    width = 0
    for char in value:
        if char == "\u200d" or unicodedata.combining(char) or unicodedata.category(char) in {"Cf", "Mn", "Me"}:
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def _take_cells(value: str, capacity: int) -> tuple[str, str]:
    used = 0
    cut = 0
    for index, char in enumerate(value):
        char_width = _cell_width(char)
        if used + char_width > capacity:
            break
        used += char_width
        cut = index + 1
    return value[:cut], value[cut:]


def wrap_prefixed(
    value: object,
    *,
    first_prefix: str = "",
    continuation: str = "  ",
    width: int = FINAL_WRAP_WIDTH,
) -> list[str]:
    """Wrap plain text using rendered monospace cell width, not byte length."""
    words = _clean(value).split()
    if not words:
        return [first_prefix.rstrip()]
    rows: list[str] = []
    prefix = first_prefix
    line = prefix
    while words:
        word = words.pop(0)
        separator = "" if line == prefix else " "
        if _cell_width(f"{line}{separator}{word}") <= width:
            line = f"{line}{separator}{word}"
            continue
        if line != prefix:
            rows.append(line.rstrip())
            prefix = continuation
            line = prefix
            words.insert(0, word)
            continue
        capacity = max(1, width - _cell_width(prefix))
        head, tail = _take_cells(word, capacity)
        rows.append(f"{prefix}{head}".rstrip())
        prefix = continuation
        line = prefix
        if tail:
            words.insert(0, tail)
    if line != prefix or not rows:
        rows.append(line.rstrip())
    return rows


def _bullet_rows(values: Sequence[object], fallback: str, *, width: int) -> list[str]:
    chosen = [_clean(value) for value in values if _clean(value)][:5] or [fallback]
    rows: list[str] = []
    for value in chosen:
        rows.extend(wrap_prefixed(value, first_prefix="- ", continuation="  ", width=width))
    return rows


def render_final_codeblock(
    *,
    owner: str,
    complete: bool,
    model: object,
    route: object,
    why: object,
    done: Sequence[object],
    issues: Sequence[object],
    next_steps: Sequence[object],
    approvals: Sequence[object],
    complete_detail: object = "",
    width: int = FINAL_WRAP_WIDTH,
) -> str:
    """Render the one definitive terminal response used by both primary topics."""
    status = "COMPLETE" if complete else "NEEDS ATTENTION"
    completion = "Yes" if complete else "No"
    detail = _clean(complete_detail)
    complete_value = f"{completion} - {detail}" if detail else completion
    lines = [
        *wrap_prefixed(f"{_clean(owner)} · {status}", width=width),
        "",
        *wrap_prefixed(model or "unverified", first_prefix="Model: ", continuation="       ", width=width),
        *wrap_prefixed(route or "unverified", first_prefix="Route: ", continuation="       ", width=width),
        *wrap_prefixed(why or "unverified", first_prefix="Why: ", continuation="     ", width=width),
        "",
        *wrap_prefixed(complete_value, first_prefix="Complete: ", continuation="  ", width=width),
        "",
        "What was done:",
        *_bullet_rows(done, "Detailed findings were not captured.", width=width),
        "",
        "Issues:",
        *_bullet_rows(issues, "None", width=width),
        "",
        "Appropriate next steps:",
        *_bullet_rows(next_steps, "No action needed.", width=width),
        "",
        "Approval needed:",
        *_bullet_rows(approvals, "None", width=width),
    ]
    return f"<pre>{html.escape(chr(10).join(lines))}</pre>"
