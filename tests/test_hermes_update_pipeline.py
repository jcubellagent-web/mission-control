from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "hermes_update_pipeline.py"
SPEC = importlib.util.spec_from_file_location("hermes_update_pipeline", MODULE_PATH)
assert SPEC and SPEC.loader
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


class HermesUpdatePipelineTests(unittest.TestCase):
    def test_verify_fails_closed_until_observation_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pipeline, "run_canary_commands", return_value={"ok": True, "results": []}
        ):
            sandbox = Path(directory) / "candidate"
            sandbox.mkdir()
            manifest = {
                "target": "a" * 40,
                "sandbox": str(sandbox),
                "canaryProfile": "trust-sandbox",
                "observationMinutes": 60,
                "sourceState": {"sourceClean": True},
                "localPatchReplay": {"ok": True, "status": "applied"},
                "canaryCommands": [["{python}", "--version"]],
                "rollback": {"prepared": True},
                "requiredGates": ["source-clean", "candidate-worktree", "local-patch-replay", "canary-command", "rollback-manifest", "observation-evidence"],
            }
            result = pipeline.verify(manifest)
        self.assertTrue(result["readyForObservation"])
        self.assertFalse(result["readyForPromotionReview"])
        self.assertIn("observation-evidence", result["failures"])
        self.assertEqual(result["promotion"], "manual-review-required")

    def test_observation_requires_duration_and_all_critical_surfaces(self) -> None:
        manifest = {
            "observationMinutes": 60,
            "observationEvidence": {
                "complete": True,
                "durationMinutes": 60,
                "checks": {
                    "gateway": True,
                    "telegramDelivery": True,
                    "cron": True,
                    "modelRouting": True,
                    "browser": True,
                    "controlTower": True,
                },
            },
        }
        self.assertTrue(pipeline.observation_check(manifest)["ok"])
        manifest["observationEvidence"]["checks"]["telegramDelivery"] = False
        check = pipeline.observation_check(manifest)
        self.assertFalse(check["ok"])
        self.assertEqual(check["failedChecks"], ["telegramDelivery"])

    def test_prepare_rejects_dirty_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pipeline, "source_state", return_value={"sourceAccessible": True, "sourceClean": False}
        ):
            config = {"sourceRepository": directory, "canaryProfile": "trust-sandbox", "requiredGates": [], "observationMinutes": 1, "productionInstall": directory}
            with self.assertRaisesRegex(RuntimeError, "dirty"):
                pipeline.prepare(config, "candidate", Path(directory))

    def test_config_forbids_automatic_promotion(self) -> None:
        config = json.loads((MODULE_PATH.parents[1] / "config" / "hermes-update-pipeline.json").read_text())
        self.assertFalse(config["automaticPromotion"])
        self.assertEqual(config["productionMutation"], "manual-only")
        self.assertTrue(config["replayLocalPatches"])
        self.assertIn("local-patch-replay", config["requiredGates"])
        self.assertTrue(config["canaryCommands"])

    def test_install_marker_is_the_only_ignored_dirty_path(self) -> None:
        config = {"sourceRepository": ".", "ignoredDirtyPaths": [".install_method"], "productionMutation": "manual-only"}
        with mock.patch.object(pipeline, "git", side_effect=[
            {"ok": True, "detail": "abc123"},
            {"ok": True, "detail": "?? .install_method\n M config.yaml"},
        ]):
            state = pipeline.source_state(config)
        self.assertFalse(state["sourceClean"])
        self.assertEqual(state["unexpectedDirtyPaths"], ["config.yaml"])

    def test_local_patch_commits_preserve_one_commit_per_line(self) -> None:
        with mock.patch.object(pipeline, "git", return_value={"ok": True, "detail": "aaa\nbbb\nccc\n"}):
            self.assertEqual(pipeline.local_patch_commits(Path("."), "base", "head"), ["aaa", "bbb", "ccc"])


if __name__ == "__main__":
    unittest.main()
