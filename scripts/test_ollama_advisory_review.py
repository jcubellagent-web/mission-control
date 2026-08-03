#!/usr/bin/env python3
"""Focused contract tests for bounded Ollama advisory inputs."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import ollama_advisory_review as subject


class AdvisoryManifestTests(unittest.TestCase):
    def write(self, payload: dict) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_sorare_manifest_requires_exact_allowlist(self) -> None:
        payload = {"schemaVersion": 1, "workflow": "sorare-prelock", "windowLabel": "GW 1", "publicStatSummary": ["public summary"], "riskCounts": {"injury": 1}, "candidateCount": 3}
        self.assertEqual(subject.read_manifest(self.write(payload), "sorare-prelock"), payload)

    def test_manifest_fails_closed_on_sensitive_key(self) -> None:
        payload = {"schemaVersion": 1, "workflow": "fcc-release-qa", "releaseLabel": "r1", "artifactChecks": ["ok"], "validationSummary": "ok", "knownRisks": [], "apiToken": "nope"}
        with self.assertRaises(ValueError):
            subject.read_manifest(self.write(payload), "fcc-release-qa")

    def test_route_snapshot_is_allowlisted(self) -> None:
        snapshot = subject.route_qa_snapshot()
        self.assertEqual(set(snapshot), {"schemaVersion", "workflow", "routeQuality", "ollamaGovernance"})
        self.assertEqual(snapshot["workflow"], "route-qa")


if __name__ == "__main__":
    unittest.main()
