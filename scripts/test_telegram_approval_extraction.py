#!/usr/bin/env python3
"""Regression checks for Telegram approval-button extraction."""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

WORKSPACE = Path(__file__).resolve().parents[2]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import jaimes_telegram_fast_ack as fast_ack
import josh_telegram_fast_ack


WATCHERS = (fast_ack, josh_telegram_fast_ack)


class TelegramApprovalExtractionTests(unittest.TestCase):
    def test_sources_after_approval_needed_do_not_become_buttons(self) -> None:
        final_text = """**Approval needed:**
- Needed before I create an Amazon developer skill or expose a webhook.

Sources:
https://developer.amazon.com/en-US/docs/alexa/build/build-your-skill-overview.html
https://developer.amazon.com/en-US/docs/alexa/account-linking/account-linking-concepts.html
"""

        for watcher in WATCHERS:
            with self.subTest(watcher=watcher.__name__):
                steps = [
                    step
                    for step in watcher.mitigation_steps_from_text(final_text)
                    if watcher.actionable_approval_step(step)
                ]

                self.assertEqual(
                    steps,
                    ["Needed before I create an Amazon developer skill or expose a webhook."],
                )

    def test_bare_urls_are_never_actionable_approval_steps(self) -> None:
        for watcher in WATCHERS:
            with self.subTest(watcher=watcher.__name__):
                self.assertFalse(
                    watcher.actionable_approval_step(
                        "https://developer.amazon.com/en-US/docs/alexa/build/build-your-skill-overview.html"
                    )
                )

    def test_formatting_is_removed_from_button_labels(self) -> None:
        final_text = """**Approval needed:**
- **Apply** the `josh2-self-improvement` proposal.

**Control Tower:** current
"""

        for watcher in WATCHERS:
            with self.subTest(watcher=watcher.__name__):
                steps = [
                    step
                    for step in watcher.mitigation_steps_from_text(final_text)
                    if watcher.actionable_approval_step(step)
                ]

                self.assertEqual(steps, ["Apply the josh2-self-improvement proposal."])
                self.assertEqual(
                    watcher.approval_button_label(steps[0]),
                    "Approve: Apply the josh2-self-improvement propo...",
                )

    def test_meta_say_this_lines_do_not_become_buttons(self) -> None:
        final_text = """**Approval needed:**
- Say “apply it” if you want me to install/activate this proposal.
"""

        for watcher in WATCHERS:
            with self.subTest(watcher=watcher.__name__):
                steps = [
                    step
                    for step in watcher.mitigation_steps_from_text(final_text)
                    if watcher.actionable_approval_step(step)
                ]

                self.assertEqual(steps, [])


if __name__ == "__main__":
    unittest.main()
