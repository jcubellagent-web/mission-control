"""Focused tests for controlled offline JCU-10 shadow evidence."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fixture = _load(
    "telegram_shadow_fixture_tested",
    ROOT / "scripts" / "telegram_shadow_fixture.py",
)
release = _load(
    "telegram_lifecycle_release_shadow_fixture_tested",
    ROOT / "scripts" / "telegram_lifecycle_release.py",
)


def _rollout() -> dict:
    return {
        "schemaVersion": 1,
        "writerLifecycleVersion": 3,
        "readerLifecycleVersions": [2, 3],
        "masterState": "shadow",
        "globalKillSwitch": False,
        "brainKillSwitch": True,
        "hosts": {"josh2": True, "jaimes": True},
        "shadowMinimumPerOwner": 20,
        "brainFixtureMinimum": 20,
        "rollback": {
            "newWorkToLegacy": False,
            "drainExistingVersionedWork": True,
        },
    }


class TelegramShadowFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        self.rollout = self.root / "rollout.json"
        self.rollout.write_text(json.dumps(_rollout()), encoding="utf-8")
        self.lifecycle_root = self.private / "lifecycle"
        self.attestation = self.private / "shadow-attestation.json"
        self.key = self.private / "shadow-attestation.key"

    def run_fixture(self, owner: str = "josh2") -> dict:
        return fixture.run_controlled_shadow_evidence(
            owner=owner,
            lifecycle_root=self.lifecycle_root,
            rollout_path=self.rollout,
            attestation_path=self.attestation,
            key_path=self.key,
        )

    def test_fixed_corpus_uses_public_lifecycle_and_creates_no_visible_effects(self) -> None:
        result = self.run_fixture()
        self.assertTrue(result["ok"])
        self.assertEqual(result["sampleCount"], len(fixture.FIXED_CORPUS))
        self.assertGreaterEqual(result["sampleCount"], 20)
        self.assertFalse(result["liveTelegramSamples"])
        self.assertEqual(stat.S_IMODE(self.attestation.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)

        with sqlite3.connect(self.lifecycle_root / "lifecycle.sqlite3") as db:
            samples = db.execute(
                "SELECT COUNT(*),SUM(matched),SUM(terminal_observed),SUM(terminal_delivered) "
                "FROM shadow_samples WHERE owner='josh2'"
            ).fetchone()
            terminal = db.execute(
                "SELECT COUNT(*) FROM work_receipts WHERE shadow_only=1 AND phase='terminal'"
            ).fetchone()[0]
            effects = db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
            pending_shadow_outbox = db.execute(
                "SELECT COUNT(*) FROM terminal_outbox WHERE state='pending'"
            ).fetchone()[0]
        expected = len(fixture.FIXED_CORPUS)
        self.assertEqual(samples, (expected, expected, expected, expected))
        self.assertEqual(terminal, expected)
        self.assertEqual(effects, 0)
        self.assertEqual(pending_shadow_outbox, expected)

        # A complete rerun is idempotent and re-verifies the same fixed rows.
        rerun = self.run_fixture()
        self.assertTrue(rerun["ok"])
        with sqlite3.connect(self.lifecycle_root / "lifecycle.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM shadow_samples").fetchone()[0], expected)

    def test_cli_has_no_caller_contract_delivery_or_sample_count_inputs(self) -> None:
        parser = fixture._parser()
        destinations: set[str] = set()
        for action in parser._actions:
            destinations.add(action.dest)
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                for child in choices.values():
                    destinations.update(item.dest for item in child._actions)
        self.assertFalse({"observed_contract", "delivered", "matched", "count"} & destinations)
        self.assertGreaterEqual(len(fixture.FIXED_CORPUS), fixture.MINIMUM_CASES)

        with fixture.deny_external_effects() as guard:
            with self.assertRaisesRegex(fixture.ShadowFixtureError, "external-effect-attempted"):
                socket.socket()
        self.assertEqual(guard.attempts, 1)

    def test_each_owner_separately_satisfies_normal_shadow_inventory_gate(self) -> None:
        host_inventory: dict[str, dict] = {}
        for owner in ("josh2", "jaimes"):
            owner_root = self.private / f"lifecycle-{owner}"
            attestation = self.private / f"attestation-{owner}.json"
            result = fixture.run_controlled_shadow_evidence(
                owner=owner,
                lifecycle_root=owner_root,
                rollout_path=self.rollout,
                attestation_path=attestation,
                key_path=self.key,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["owner"], owner)
            host_inventory[owner] = release.inventory_local(
                owner_root / "lifecycle.sqlite3",
                writer_version=3,
                expected_owner=owner,
            )
            self.assertTrue(host_inventory[owner]["ok"])
            self.assertEqual(host_inventory[owner]["shadowSamples"], len(fixture.FIXED_CORPUS))
            self.assertEqual(host_inventory[owner]["cleanShadowSamples"], len(fixture.FIXED_CORPUS))
            self.assertEqual(host_inventory[owner]["openShadowReceipts"], 0)
            self.assertEqual(host_inventory[owner]["unsampledShadowReceipts"], 0)
        promoted = _rollout()
        promoted["masterState"] = "josh2"
        gate = release.verify_shadow_evidence_gate(
            promoted,
            {"ok": True, "hosts": host_inventory},
        )
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["owners"]["josh2"]["clean"], len(fixture.FIXED_CORPUS))
        self.assertEqual(gate["owners"]["jaimes"]["clean"], len(fixture.FIXED_CORPUS))

    def test_loaded_renderer_fingerprint_is_stable_across_hash_seeds(self) -> None:
        code = (
            "import sys;"
            f"sys.path.insert(0,{str(ROOT / 'scripts')!r});"
            "import telegram_shadow_fixture as fixture;"
            "print(fixture.loaded_callable_digests()['renderLiveCard'])"
        )
        digests: set[str] = set()
        for seed in ("1", "2", "3", "4"):
            environment = dict(os.environ)
            environment["PYTHONHASHSEED"] = seed
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            digests.add(result.stdout.strip())
        self.assertEqual(len(digests), 1)

    def test_signature_db_tampering_and_code_drift_fail_verification(self) -> None:
        self.assertTrue(self.run_fixture()["ok"])
        value = json.loads(self.attestation.read_text(encoding="utf-8"))
        value["sampleCount"] -= 1
        self.attestation.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(self.attestation, 0o600)
        tampered = fixture.verify_attestation(
            attestation_path=self.attestation,
            key_path=self.key,
            lifecycle_root=self.lifecycle_root,
        )
        self.assertFalse(tampered["ok"])
        self.assertIn("attestation-signature-mismatch", tampered["problems"])

        # Restore a valid signature, then alter one durable observation.
        self.assertTrue(self.run_fixture()["ok"])
        with sqlite3.connect(self.lifecycle_root / "lifecycle.sqlite3") as db:
            db.execute(
                "UPDATE shadow_samples SET terminal_delivered=0 "
                "WHERE id=(SELECT MIN(id) FROM shadow_samples)"
            )
        database_tamper = fixture.verify_attestation(
            attestation_path=self.attestation,
            key_path=self.key,
            lifecycle_root=self.lifecycle_root,
        )
        self.assertFalse(database_tamper["ok"])
        self.assertIn("attestation-database-evidence-mismatch", database_tamper["problems"])

        # Code digests cover the lifecycle and both live owner adapters.
        drift_root = self.root / "drift"
        scripts = drift_root / "scripts"
        scripts.mkdir(parents=True)
        for filename in (*fixture.SOURCE_FILENAMES, Path(fixture.__file__).name):
            shutil.copy2(ROOT / "scripts" / filename, scripts / filename)
        with (scripts / "josh_telegram_fast_ack.py").open("a", encoding="utf-8") as handle:
            handle.write("\n# controlled test drift\n")
        code_drift = fixture.verify_attestation(
            attestation_path=self.attestation,
            key_path=self.key,
            lifecycle_root=self.lifecycle_root,
            source_root=drift_root,
        )
        self.assertFalse(code_drift["ok"])
        self.assertIn("attestation-implementation-drift", code_drift["problems"])

    def test_conflicting_observation_owner_mix_and_nonshadow_policy_fail_closed(self) -> None:
        self.assertTrue(self.run_fixture()["ok"])
        with sqlite3.connect(self.lifecycle_root / "lifecycle.sqlite3") as db:
            db.execute(
                "UPDATE shadow_samples SET legacy_contract='final-only' "
                "WHERE tier=3 AND id=(SELECT MIN(id) FROM shadow_samples WHERE tier=3)"
            )
        with self.assertRaisesRegex(
            fixture.ShadowFixtureError,
            "controlled-shadow-existing-observation-conflict",
        ):
            self.run_fixture()

        other_root = self.private / "owner-mix"
        other_attestation = self.private / "owner-mix.json"
        self.assertTrue(fixture.run_controlled_shadow_evidence(
            owner="josh2",
            lifecycle_root=other_root,
            rollout_path=self.rollout,
            attestation_path=other_attestation,
            key_path=self.key,
        )["ok"])
        with self.assertRaisesRegex(
            fixture.ShadowFixtureError,
            "controlled-shadow-owner-scope-conflict",
        ):
            fixture.run_controlled_shadow_evidence(
                owner="jaimes",
                lifecycle_root=other_root,
                rollout_path=self.rollout,
                attestation_path=other_attestation,
                key_path=self.key,
            )

        invalid = _rollout()
        invalid["masterState"] = "josh2"
        self.rollout.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaisesRegex(
            fixture.ShadowFixtureError,
            "controlled-shadow-policy-required",
        ):
            fixture.run_controlled_shadow_evidence(
                owner="josh2",
                lifecycle_root=self.private / "nonshadow",
                rollout_path=self.rollout,
                attestation_path=self.private / "nonshadow.json",
                key_path=self.key,
            )


if __name__ == "__main__":
    unittest.main()
