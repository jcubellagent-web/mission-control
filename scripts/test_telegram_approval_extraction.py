#!/usr/bin/env python3
"""Regression checks for Telegram approval-button extraction."""

from __future__ import annotations

import unittest
import importlib.util
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) in sys.path:
    sys.path.remove(str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR))


def load_canonical(name: str, filename: str):
    path = SCRIPT_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import canonical watcher {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fast_ack = load_canonical("canonical_jaimes_telegram_fast_ack_for_approvals", "jaimes_telegram_fast_ack.py")
josh_telegram_fast_ack = load_canonical("canonical_josh_telegram_fast_ack_for_approvals", "josh_telegram_fast_ack.py")


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
