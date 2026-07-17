#!/usr/bin/env python3
"""Regression tests for the local-only X post analyzer."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze_posts.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "collected_posts.json"


def run_analyzer(*arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


class AnalyzePostsTests(unittest.TestCase):
    def test_fixture_dedupes_and_flags_promotional_coordination(self) -> None:
        first = run_analyzer("--input", str(FIXTURE), "--compact")
        second = run_analyzer("--input", str(FIXTURE), "--compact")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        result = json.loads(first.stdout)

        self.assertTrue(result["ok"])
        coverage = result["coverage"]
        self.assertEqual(coverage["inputPostCount"], 8)
        self.assertEqual(coverage["rejectedPostCount"], 1)
        self.assertEqual(coverage["duplicateStatusUrlCount"], 1)
        self.assertEqual(coverage["exactTextDuplicateCount"], 1)
        self.assertEqual(coverage["nearTextDuplicateCount"], 1)
        self.assertEqual(coverage["analyzedPostCount"], 4)

        assessment = result["manipulationAssessment"]
        self.assertEqual(assessment["risk"], "high")
        self.assertEqual(assessment["indicatorCounts"]["repeated_cross_author_text"], 2)
        self.assertEqual(assessment["indicatorCounts"]["guaranteed_return_language"], 1)
        self.assertFalse(result["methodology"]["networkOrBrowserAccess"])
        self.assertIn("not financial advice", result["decisionUse"])

    def test_reads_raw_posts_from_stdin(self) -> None:
        payload = json.dumps(
            [
                {
                    "statusUrl": "https://x.com/example/status/200",
                    "authorHandle": "@Example",
                    "timestamp": "2026-07-17T20:00:00Z",
                    "text": "Not bearish after the verified upgrade.",
                }
            ]
        )
        completed = run_analyzer("--compact", stdin=payload)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["coverage"]["analyzedPostCount"], 1)
        self.assertEqual(result["sentiment"]["postVotes"]["bullish"], 1)
        self.assertEqual(result["sentiment"]["label"], "unclear")

    def test_invalid_json_fails_without_echoing_input(self) -> None:
        secret_marker = "do-not-echo-this-marker"
        completed = run_analyzer("--compact", stdin="{" + secret_marker)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        error = json.loads(completed.stderr)
        self.assertEqual(error["error"], "invalid-input")
        self.assertNotIn(secret_marker, completed.stderr)

    def test_invalid_top_level_shape_fails(self) -> None:
        completed = run_analyzer("--compact", stdin=json.dumps({"unexpected": True}))
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(json.loads(completed.stderr)["error"], "invalid-input")


if __name__ == "__main__":
    unittest.main()
