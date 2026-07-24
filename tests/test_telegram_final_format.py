from __future__ import annotations

import html

from telegram_final_format import render_final_codeblock


def test_shared_final_codeblock_has_separate_metadata_and_bounded_rows() -> None:
    rendered = render_final_codeblock(
        owner="JAIMES",
        complete=True,
        model="openai-codex/gpt-5.6-sol",
        route="JAIMES verified execution",
        why="heavy workhorse reasoning",
        complete_detail="Run the current Telegram health check",
        done=[
            "Health checks passed twice, 31 seconds apart.",
            "Topic 17 ownership resolved exclusively to JAIMES.",
            "Gateway, Telegram, and fast-ack were healthy.",
        ],
        issues=[],
        next_steps=["No action needed."],
        approvals=[],
    )

    assert rendered.startswith("<pre>JAIMES · COMPLETE\n\n")
    assert rendered.endswith("</pre>")
    assert "\nModel: openai-codex/gpt-5.6-sol\nRoute: JAIMES verified execution\nWhy: heavy workhorse reasoning\n" in rendered
    assert "<blockquote>" not in rendered
    body = html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>"))
    assert max(map(len, body.splitlines())) <= 38
    assert body.count("What was done:") == 1
    assert body.count("Approval needed:") == 1


def test_shared_final_codeblock_uses_hanging_indents() -> None:
    rendered = render_final_codeblock(
        owner="JOSH 2.0",
        complete=False,
        model="codex/gpt-5.6-luna",
        route="Josh 2.0 Inbox",
        why="read-only health and status check",
        done=["A deliberately long finding wraps cleanly beneath its bullet marker on a narrow phone."],
        issues=["A deliberately long issue wraps cleanly beneath its bullet marker on a narrow phone."],
        next_steps=["Retry after collecting the missing evidence."],
        approvals=["Approve the next safe check."],
    )
    body = html.unescape(rendered.removeprefix("<pre>").removesuffix("</pre>"))
    assert any(line.startswith("  ") for line in body.splitlines())
    assert all(len(line) <= 38 for line in body.splitlines())
