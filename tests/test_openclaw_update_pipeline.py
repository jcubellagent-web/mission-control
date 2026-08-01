from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "openclaw_update_pipeline.py"
SPEC = importlib.util.spec_from_file_location("openclaw_update_pipeline", MODULE_PATH)
assert SPEC and SPEC.loader
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


class OpenClawUpdatePipelineTests(unittest.TestCase):
    def test_prerelease_is_rejected_for_production(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "stable"):
            pipeline.reject_prerelease("2026.7.2-beta.5", {"allowPrereleasePromotion": False})

    def test_verify_fails_closed_without_observation(self) -> None:
        manifest = {
            "target": "2026.7.2",
            "sandbox": "/private/tmp/candidate",
            "candidate": "/private/tmp/candidate/openclaw",
            "productionBaseline": {"ok": True},
            "candidateInstall": {"ok": True},
            "candidateVersion": {"ok": True},
            "canaryCommands": [["{candidate}", "--help"]],
            "criticalSurfaces": ["gateway", "telegramDelivery"],
            "observationMinutes": 60,
            "rollback": {"prepared": True},
            "requiredGates": ["production-baseline", "candidate-install", "candidate-version", "synthetic-canary", "observation-evidence", "rollback-manifest"],
        }
        with mock.patch.object(pipeline, "synthetic_canary", return_value={"ok": True, "results": []}):
            result = pipeline.verify(manifest)
        self.assertTrue(result["readyForObservation"])
        self.assertFalse(result["readyForPromotionReview"])
        self.assertEqual(result["failures"], ["observation-evidence"])

    def test_observation_requires_every_configured_surface(self) -> None:
        manifest = {
            "observationMinutes": 60,
            "criticalSurfaces": ["gateway", "telegramDelivery"],
            "observationEvidence": {"complete": True, "durationMinutes": 60, "checks": {"gateway": True, "telegramDelivery": False}},
        }
        result = pipeline.observation_check(manifest)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failedChecks"], ["telegramDelivery"])

    def test_config_disables_automatic_promotion(self) -> None:
        config = json.loads((MODULE_PATH.parents[1] / "config" / "openclaw-update-pipeline.json").read_text())
        self.assertFalse(config["automaticPromotion"])
        self.assertFalse(config["allowPrereleasePromotion"])
        self.assertEqual(config["productionMutation"], "manual-only")


if __name__ == "__main__":
    unittest.main()
