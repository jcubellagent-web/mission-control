from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brain_fixture_suite as suite
import telegram_lifecycle_release as release


class BrainFixtureSuiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.shared = tempfile.TemporaryDirectory(prefix="brain-fixture-suite-shared-")
        cls.shared_root = Path(cls.shared.name)
        cls.shared_root.chmod(0o700)
        cls.real_result = suite.run_suite(cls.shared_root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.shared.cleanup()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="brain-fixture-suite-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.attestation = self.root / suite.ATTESTATION_FILENAME
        self.key = self.root / suite.SIGNING_KEY_FILENAME
        shutil.copyfile(self.shared_root / suite.ATTESTATION_FILENAME, self.attestation)
        shutil.copyfile(self.shared_root / suite.SIGNING_KEY_FILENAME, self.key)
        self.attestation.chmod(0o600)
        self.key.chmod(0o600)

    def resign(self, value: dict[str, object]) -> None:
        key = self.key.read_bytes()
        core = {
            field: item
            for field, item in value.items()
            if field not in {
                "attestationDigest", "signatureAlgorithm", "signingKeyId", "signature",
            }
        }
        signed = suite._signed_document(core, key)
        suite._atomic_private_write(
            self.attestation,
            suite._canonical_json(signed) + b"\n",
        )

    def test_real_complete_run_passes_and_is_private(self) -> None:
        self.assertTrue(self.real_result["ok"])
        self.assertGreaterEqual(self.real_result["flowCaseCount"], 20)
        self.assertEqual(
            stat.S_IMODE((self.shared_root / suite.ATTESTATION_FILENAME).stat().st_mode),
            0o600,
        )
        verified = suite.verify_attestation(self.attestation)
        self.assertTrue(verified["ok"], verified)
        self.assertEqual(verified["flowCaseCount"], len(suite.FLOW_SPECS))
        self.assertEqual(verified["faultCaseCount"], len(suite.FAULT_SPECS))
        value = json.loads(self.attestation.read_text(encoding="utf-8"))
        oversize = next(
            row for row in value["cases"] if row["caseId"] == "oversize-declaration"
        )
        self.assertEqual(oversize["outcome"], "unsupported")
        self.assertRegex(oversize["evidenceDigest"], r"^[0-9a-f]{64}$")

    def test_caller_booleans_cannot_record_or_fake_eligibility(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            suite._parser().parse_args([
                "--private-root", str(self.root), "--privacy-ok", "--cleanup-ok",
            ])
        command = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "brain_media_intake.py"),
                "--root", str(self.root / "brain-store"),
                "record-fixture", "--media-class", "text", "--outcome", "ok",
                "--privacy-ok", "--cleanup-ok",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(command.returncode, 0)
        self.assertNotIn('"eligible": true', command.stdout.lower())

    def test_tamper_blocks_even_when_mode_is_private(self) -> None:
        value = json.loads(self.attestation.read_text())
        value["flowCaseCount"] = int(value["flowCaseCount"]) + 1
        suite._atomic_private_write(self.attestation, suite._canonical_json(value) + b"\n")
        verified = suite.verify_attestation(self.attestation)
        self.assertFalse(verified["ok"])
        self.assertIn("attestation-signature-invalid", verified["problems"])

    def test_insecure_mode_blocks(self) -> None:
        self.attestation.chmod(0o644)
        verified = suite.verify_attestation(self.attestation)
        self.assertFalse(verified["ok"])
        self.assertEqual(verified["status"], "blocked")

    def test_cryptographically_valid_under_minimum_attestation_blocks(self) -> None:
        value = json.loads(self.attestation.read_text())
        value["cases"] = value["cases"][:19]
        value["flowCaseCount"] = 19
        value["cleanFlowCaseCount"] = 19
        value["faultCaseCount"] = 0
        value["cleanFaultCaseCount"] = 0
        value["casesDigest"] = suite._sha256(suite._canonical_json(value["cases"]))
        self.resign(value)
        verified = suite.verify_attestation(self.attestation)
        self.assertFalse(verified["ok"])
        self.assertIn("attestation-minimum-not-met", verified["problems"])

    def test_signed_remnant_claim_blocks(self) -> None:
        value = json.loads(self.attestation.read_text())
        value["cleanup"]["retrievalRemnants"] = 1
        self.resign(value)
        verified = suite.verify_attestation(self.attestation)
        self.assertFalse(verified["ok"])
        self.assertIn("attestation-remnants-present", verified["problems"])

    def test_production_implementation_change_invalidates_attestation(self) -> None:
        for component in (
            "brainMediaIntake",
            "telegramGatewayLifecycle",
            "telegramLifecycleRelease",
        ):
            with self.subTest(component=component):
                changed = dict(suite.production_implementation_digests())
                changed[component] = "0" * 64
                with mock.patch.object(
                    suite, "production_implementation_digests", return_value=changed,
                ):
                    verified = suite.verify_attestation(self.attestation)
                self.assertFalse(verified["ok"])
                self.assertIn(
                    "attestation-implementation-map-invalid", verified["problems"],
                )

    def test_execution_checkpoint_is_fsynced_before_fixture_root_removal(self) -> None:
        checkpoint_root = self.root / "checkpoint-run"
        checkpoint_root.mkdir(mode=0o700)
        observed: list[str] = []
        remove = suite._remove_fixture_work_root

        def observe(work_root: Path, private_root: Path) -> None:
            value = json.loads(
                (private_root / suite.ATTESTATION_FILENAME).read_text(encoding="utf-8")
            )
            observed.append(str(value.get("status")))
            self.assertEqual(value.get("cleanup", {}).get("workDirectoryRemnants"), 1)
            self.assertEqual(
                stat.S_IMODE((private_root / suite.ATTESTATION_FILENAME).stat().st_mode),
                0o600,
            )
            remove(work_root, private_root)

        with mock.patch.object(suite, "_remove_fixture_work_root", side_effect=observe):
            result = suite.run_suite(checkpoint_root)
        self.assertTrue(result["ok"])
        self.assertEqual(observed, ["cleanup-pending"])

    def test_release_gate_requires_attestation_only_for_brain_production(self) -> None:
        disabled = {
            "masterState": "josh2",
            "globalKillSwitch": False,
            "brainKillSwitch": True,
            "hosts": {"josh2": True, "jaimes": True},
        }
        self.assertTrue(release.verify_brain_fixture_gate(disabled, None)["ok"])
        enabled = {**disabled, "brainKillSwitch": False}
        blocked = release.verify_brain_fixture_gate(enabled, None)
        self.assertFalse(blocked["ok"])
        verified = release.verify_brain_fixture_gate(enabled, self.attestation)
        self.assertTrue(verified["ok"], verified)
        self.assertGreaterEqual(verified["flowCaseCount"], 20)

    def test_suite_rejects_nonpreexisting_or_nonprivate_root(self) -> None:
        missing = self.root / "missing"
        with self.assertRaisesRegex(suite.FixtureSuiteError, "must-preexist"):
            suite.run_suite(missing)
        self.root.chmod(0o755)
        with self.assertRaisesRegex(suite.FixtureSuiteError, "private-root-invalid"):
            suite.run_suite(self.root)


if __name__ == "__main__":
    unittest.main()
