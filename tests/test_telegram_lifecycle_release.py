"""Focused offline tests for JCU-10 release and canary safety gates."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = _load(
    "telegram_lifecycle_release_tested",
    ROOT / "scripts" / "telegram_lifecycle_release.py",
)
stress = _load(
    "telegram_response_contract_stress_release_tested",
    ROOT / "scripts" / "telegram_response_contract_stress.py",
)


def _create_inventory(path: Path, rows: list[dict], *, owner: str = "josh2") -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE work_receipts(
          work_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          phase TEXT NOT NULL,
          delivery_state TEXT NOT NULL,
          lifecycle_version INTEGER NOT NULL,
          shadow_only INTEGER NOT NULL,
          current_owner TEXT NOT NULL
        );
        CREATE TABLE effects(work_id TEXT NOT NULL, state TEXT NOT NULL);
        CREATE TABLE terminal_outbox(work_id TEXT NOT NULL, state TEXT NOT NULL);
        CREATE TABLE shadow_samples(
          sample_id TEXT PRIMARY KEY,
          owner TEXT NOT NULL,
          work_id TEXT NOT NULL,
          delivery_tier INTEGER NOT NULL,
          classifier_reason TEXT NOT NULL,
          legacy_contract TEXT NOT NULL,
          matched INTEGER NOT NULL,
          terminal_observed INTEGER NOT NULL,
          terminal_delivered INTEGER NOT NULL,
          created_at TEXT NOT NULL
        );
        """
    )
    for row in rows:
        db.execute(
            "INSERT INTO work_receipts VALUES(?,?,?,?,?,?,?)",
            (
                row["work_id"],
                row.get("created_at", "2026-07-20T10:00:00Z"),
                row.get("phase", "terminal"),
                row.get("delivery_state", "delivered"),
                row.get("lifecycle_version", 3),
                int(bool(row.get("shadow_only", False))),
                row.get("current_owner", owner),
            ),
        )
        if row.get("shadow_sample") is not None:
            db.execute(
                "INSERT INTO shadow_samples VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    f"sample-{row['work_id']}",
                    row.get("sample_owner", owner),
                    row["work_id"],
                    row.get("delivery_tier", 3),
                    row.get("classifier_reason", "long-running"),
                    row.get("legacy_contract", "reaction-card-final"),
                    int(bool(row.get("shadow_sample"))),
                    int(bool(row.get("shadow_terminal_observed", True))),
                    int(bool(row.get("shadow_terminal_delivered", True))),
                    row.get("created_at", "2026-07-20T10:00:00Z"),
                ),
            )
        if row.get("effect_state"):
            db.execute(
                "INSERT INTO effects VALUES(?,?)",
                (row["work_id"], row["effect_state"]),
            )
        if row.get("outbox_state"):
            db.execute(
                "INSERT INTO terminal_outbox VALUES(?,?)",
                (row["work_id"], row["outbox_state"]),
            )
    db.commit()
    db.close()


def _valid_rollout(state: str = "off") -> dict:
    return {
        "schemaVersion": 1,
        "writerLifecycleVersion": 3,
        "readerLifecycleVersions": [2, 3],
        "masterState": state,
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


def _valid_rollback_plan() -> dict:
    return {
        "restoreFromBackup": True,
        "preserveVersionedDrain": True,
        "nMinusOneVersion": 2,
        "steps": list(release.REQUIRED_ROLLBACK_STEPS),
    }


class LifecycleReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def make_artifact(self, name: str, content: str = "LIFECYCLE_VERSION = 3\n") -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def make_manifest(self) -> tuple[dict, Path, Path]:
        source_artifacts: dict[str, dict[str, str]] = {}
        entrypoints: dict[str, dict[str, str]] = {"josh2": {}, "jaimes": {}}
        jaimes_entrypoint: Path | None = None
        for artifact_name, scope in release.REQUIRED_ARTIFACT_SCOPES.items():
            content = f"# {artifact_name}\nLIFECYCLE_VERSION = 3\n"
            if artifact_name.endswith("launchd"):
                content = f"<plist><dict><key>Label</key><string>{artifact_name}</string></dict></plist>\n"
            source = self.make_artifact(f"source-{artifact_name}", content)
            declaration: dict[str, str] = {"path": str(source), "scope": scope, "version": "3"}
            if artifact_name == "brain-actions-launchd":
                declaration.pop("version")
                declaration["expectedSha256"] = release.sha256_file(source)
            source_artifacts[artifact_name] = declaration
            targets = release.REQUIRED_HOSTS if scope == "shared" else (scope,)
            for host_name in targets:
                deployed = self.make_artifact(f"{host_name}-{artifact_name}", content)
                entrypoints[host_name][artifact_name] = str(deployed)
                if host_name == "jaimes" and artifact_name == "telegram-lifecycle":
                    jaimes_entrypoint = deployed
        assert jaimes_entrypoint is not None
        josh_db = self.root / "josh-lifecycle.sqlite3"
        jaimes_db = self.root / "jaimes-lifecycle.sqlite3"
        drained = [{
            "work_id": "work-a",
            "phase": "terminal",
            "delivery_state": "delivered",
            "effect_state": "delivered",
            "outbox_state": "delivered",
        }]
        _create_inventory(josh_db, drained, owner="josh2")
        _create_inventory(jaimes_db, drained, owner="jaimes")
        return (
            {
                "schemaVersion": 2,
                "sourceArtifacts": source_artifacts,
                "hosts": {
                    "josh2": {
                        "transport": "local",
                        "lifecycleDb": str(josh_db),
                        "entrypoints": entrypoints["josh2"],
                    },
                    "jaimes": {
                        "transport": "local",
                        "lifecycleDb": str(jaimes_db),
                        "entrypoints": entrypoints["jaimes"],
                    },
                },
                "rolloutHistory": ["off"],
                "rollbackPlan": _valid_rollback_plan(),
            },
            josh_db,
            jaimes_entrypoint,
        )

    def test_rollout_transition_order_is_exact_and_non_skippable(self):
        for current, target in zip(release.ROLLOUT_SEQUENCE, release.ROLLOUT_SEQUENCE[1:]):
            with self.subTest(current=current, target=target):
                self.assertTrue(release.validate_transition(current, target)["ok"])
        self.assertFalse(release.validate_transition("off", "josh2")["ok"])
        self.assertFalse(release.validate_transition("shadow", "all")["ok"])
        self.assertFalse(release.validate_transition("all", "jaimes")["ok"])

    def test_rollout_and_rollback_require_kill_switches_n_minus_one_and_ordered_plan(self):
        policy = release.validate_rollout_policy(_valid_rollout())
        self.assertTrue(policy["ok"])
        self.assertTrue(
            release.verify_rollback_plan(_valid_rollback_plan(), writer_version=3)["ok"]
        )

        broken = _valid_rollout()
        del broken["hosts"]["jaimes"]
        broken["readerLifecycleVersions"] = [3]
        broken["rollback"]["drainExistingVersionedWork"] = False
        result = release.validate_rollout_policy(broken)
        self.assertFalse(result["ok"])
        self.assertIn("host-kill-switches-missing", result["problems"])
        self.assertIn("n-and-n-minus-one-readers-required", result["problems"])
        self.assertIn("versioned-drain-policy-required", result["problems"])

        plan = _valid_rollback_plan()
        plan["steps"] = list(reversed(plan["steps"]))
        self.assertFalse(release.verify_rollback_plan(plan, writer_version=3)["ok"])

        rollback = _valid_rollout("off")
        rollback["writerLifecycleVersion"] = 2
        rollback["rollback"]["newWorkToLegacy"] = True
        rolled_back = release.validate_rollout_policy(rollback)
        self.assertTrue(rolled_back["ok"])
        self.assertEqual(rolled_back["readerVersions"], [2, 3])

        rollback["readerLifecycleVersions"] = [1, 2]
        self.assertFalse(release.validate_rollout_policy(rollback)["ok"])

    def test_read_only_inventory_reports_counts_and_blocks_open_pre_cutover_work(self):
        database = self.root / "inventory.sqlite3"
        _create_inventory(
            database,
            [
                {
                    "work_id": "delivered-v2",
                    "lifecycle_version": 2,
                    "phase": "terminal",
                    "delivery_state": "delivered",
                    "effect_state": "delivered",
                    "outbox_state": "delivered",
                },
                {
                    "work_id": "pending-v3",
                    "phase": "working",
                    "delivery_state": "pending",
                    "effect_state": "sending",
                },
            ],
        )
        before_mode = stat.S_IMODE(database.stat().st_mode)
        result = release.inventory_local(database, writer_version=3)
        self.assertTrue(result["ok"])
        self.assertEqual(result["totalReceipts"], 2)
        self.assertEqual(result["nMinusOneReceipts"], 1)
        self.assertEqual(result["openReceipts"], 1)
        self.assertEqual(result["openEffects"], 1)
        self.assertEqual(stat.S_IMODE(database.stat().st_mode), before_mode)

    def test_cutover_inventory_ignores_new_work_but_drains_every_older_receipt(self):
        database = self.root / "cutover-inventory.sqlite3"
        _create_inventory(
            database,
            [
                {
                    "work_id": "older-delivered",
                    "created_at": "2026-07-20T10:00:00Z",
                    "phase": "terminal",
                    "delivery_state": "delivered",
                    "outbox_state": "delivered",
                },
                {
                    "work_id": "newer-active",
                    "created_at": "2026-07-20T14:00:00Z",
                    "phase": "working",
                    "delivery_state": "pending",
                    "effect_state": "sending",
                },
            ],
        )
        result = release.inventory_local(
            database,
            writer_version=3,
            cutover_at="2026-07-20T12:00:00Z",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["totalReceipts"], 2)
        self.assertEqual(result["preCutoverReceipts"], 1)
        self.assertEqual(result["openReceipts"], 0)
        self.assertEqual(result["openEffects"], 0)

    def test_cross_host_drain_gate_requires_both_hosts_and_zero_open_receipts(self):
        manifest, josh_db, _ = self.make_manifest()
        drained = release.cross_host_inventory(manifest, writer_version=3)
        self.assertTrue(drained["ok"])
        self.assertEqual(drained["hostCount"], 2)

        josh_db.unlink()
        _create_inventory(
            josh_db,
            [{"work_id": "open", "phase": "working", "delivery_state": "pending"}],
        )
        blocked = release.cross_host_inventory(manifest, writer_version=3)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["status"], "blocked")

    def test_ssh_inventory_probe_is_batch_read_only_and_returns_counts_only(self):
        response = {
            "ok": True,
            "totalReceipts": 4,
            "preCutoverReceipts": 4,
            "openReceipts": 0,
            "indeterminateReceipts": 0,
            "nMinusOneReceipts": 1,
            "unsupportedReceipts": 0,
            "openEffects": 0,
            "openTerminalOutbox": 0,
            "shadowReceipts": 0,
            "openShadowReceipts": 0,
            "shadowPendingOutbox": 0,
            "shadowSamples": 0,
            "cleanShadowSamples": 0,
            "dirtyShadowSamples": 0,
            "unobservedShadowSamples": 0,
            "unsampledShadowReceipts": 0,
            "shadowOwnerMismatches": 0,
        }
        with patch.object(
            release.subprocess,
            "run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout=json.dumps(response),
                stderr="",
            ),
        ) as run:
            result = release.inventory_host(
                {
                    "transport": "ssh",
                    "target": "jaimes",
                    "python": "/usr/bin/python3",
                    "lifecycleDb": "/private/lifecycle.sqlite3",
                },
                writer_version=3,
            )
        self.assertEqual(result, response)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["ssh", "-o", "BatchMode=yes", "jaimes"])
        program = run.call_args.kwargs["input"]
        self.assertIn("mode=ro", program)
        self.assertIn("query_only", program)
        self.assertNotIn("DELETE FROM", program.upper())
        self.assertEqual(command[-2:], ["-", "josh2"])

    def test_version_checksum_and_deployed_entrypoint_parity_matrix(self):
        manifest, _, jaimes_entrypoint = self.make_manifest()
        matched = release.parity_matrix(manifest)
        self.assertTrue(matched["ok"])
        self.assertEqual(len(matched["rows"]), matched["expectedRowCount"])
        self.assertTrue(all(row["versionMatch"] for row in matched["rows"]))
        self.assertTrue(all(row["checksumMatch"] for row in matched["rows"]))

        jaimes_entrypoint.write_text("LIFECYCLE_VERSION = 2\n", encoding="utf-8")
        mismatch = release.parity_matrix(manifest)
        self.assertFalse(mismatch["ok"])
        self.assertTrue(any(not row["ok"] for row in mismatch["rows"]))

    def test_parity_requires_complete_scoped_artifact_inventory(self):
        manifest, _, _ = self.make_manifest()
        missing_name = "brain-gateway-actions"
        declaration = manifest["sourceArtifacts"].pop(missing_name)
        missing = release.parity_matrix(manifest)
        self.assertFalse(missing["ok"])
        self.assertIn(f"required-artifact-missing:{missing_name}", missing["problems"])

        manifest["sourceArtifacts"][missing_name] = declaration
        manifest["sourceArtifacts"]["jaimes-fast-ack"]["scope"] = "shared"
        wrong_scope = release.parity_matrix(manifest)
        self.assertFalse(wrong_scope["ok"])
        self.assertIn("artifact-scope-mismatch:jaimes-fast-ack", wrong_scope["problems"])

    def test_full_preflight_combines_inventory_parity_rollout_and_rollback(self):
        manifest, _, jaimes_entrypoint = self.make_manifest()
        result = release.release_preflight(manifest, _valid_rollout())
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["passedCount"], result["checkCount"])

        rollback = _valid_rollout("off")
        rollback["writerLifecycleVersion"] = 2
        rollback["rollback"]["newWorkToLegacy"] = True
        rollback_result = release.release_preflight(manifest, rollback)
        self.assertTrue(rollback_result["ok"])
        self.assertEqual(
            rollback_result["checks"]["rolloutPolicy"]["readerVersions"],
            [2, 3],
        )

        jaimes_entrypoint.write_text("LIFECYCLE_VERSION = 2\n", encoding="utf-8")
        blocked = release.release_preflight(manifest, _valid_rollout())
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["status"], "blocked")

    def test_terminal_shadow_outbox_is_audited_but_not_a_visible_drain_blocker(self):
        manifest, josh_db, _ = self.make_manifest()
        josh_db.unlink()
        _create_inventory(
            josh_db,
            [{
                "work_id": "terminal-shadow",
                "phase": "terminal",
                "delivery_state": "pending",
                "outbox_state": "pending",
                "shadow_only": True,
                "shadow_sample": True,
            }],
            owner="josh2",
        )
        inventory = release.cross_host_inventory(manifest, writer_version=3)
        self.assertTrue(inventory["ok"])
        self.assertEqual(inventory["hosts"]["josh2"]["openReceipts"], 0)
        self.assertEqual(inventory["hosts"]["josh2"]["openTerminalOutbox"], 0)
        self.assertEqual(inventory["hosts"]["josh2"]["shadowPendingOutbox"], 1)

    def test_shadow_promotion_gate_requires_twenty_clean_terminal_observations_per_owner(self):
        manifest, josh_db, _ = self.make_manifest()
        jaimes_db = Path(manifest["hosts"]["jaimes"]["lifecycleDb"])
        for database, owner in ((josh_db, "josh2"), (jaimes_db, "jaimes")):
            database.unlink()
            _create_inventory(
                database,
                [{
                    "work_id": f"{owner}-shadow-{index}",
                    "phase": "terminal",
                    "delivery_state": "pending",
                    "outbox_state": "pending",
                    "shadow_only": True,
                    "shadow_sample": True,
                } for index in range(20)],
                owner=owner,
            )
        inventory = release.cross_host_inventory(manifest, writer_version=3)
        verified = release.verify_shadow_evidence_gate(_valid_rollout("josh2"), inventory)
        self.assertTrue(verified["ok"])
        self.assertEqual(verified["owners"]["josh2"]["clean"], 20)
        self.assertEqual(verified["owners"]["jaimes"]["clean"], 20)

        with sqlite3.connect(josh_db) as db:
            db.execute(
                "UPDATE shadow_samples SET terminal_observed=0,terminal_delivered=0 "
                "WHERE sample_id=(SELECT MIN(sample_id) FROM shadow_samples)"
            )
        unobserved = release.verify_shadow_evidence_gate(
            _valid_rollout("josh2"),
            release.cross_host_inventory(manifest, writer_version=3),
        )
        self.assertFalse(unobserved["ok"])
        self.assertIn("josh2:terminal-observation-missing", unobserved["problems"])

        with sqlite3.connect(josh_db) as db:
            db.execute(
                "UPDATE shadow_samples SET terminal_observed=1,terminal_delivered=1"
            )
            db.execute("UPDATE shadow_samples SET matched=0 WHERE sample_id=(SELECT MIN(sample_id) FROM shadow_samples)")
        dirty = release.verify_shadow_evidence_gate(
            _valid_rollout("josh2"),
            release.cross_host_inventory(manifest, writer_version=3),
        )
        self.assertFalse(dirty["ok"])
        self.assertIn("josh2:unclean-sample", dirty["problems"])

    def test_backup_and_install_are_dry_run_by_default_and_explicit_when_applied(self):
        source = self.make_artifact("new-entrypoint.py")
        destination = self.make_artifact("installed-entrypoint.py", "LIFECYCLE_VERSION = 2\n")
        backup_dir = self.root / "private-backup"
        source_hash = release.sha256_file(source)
        original = destination.read_bytes()

        plan = release.install_artifact(
            source,
            destination,
            backup_dir,
            name="lifecycle",
            expected_sha256=source_hash,
        )
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(destination.read_bytes(), original)
        self.assertFalse(backup_dir.exists())

        with self.assertRaises(release.ReleaseError):
            release.install_artifact(
                source,
                destination,
                backup_dir,
                name="lifecycle",
                expected_sha256=source_hash,
                apply=True,
            )
        self.assertEqual(destination.read_bytes(), original)

        applied = release.install_artifact(
            source,
            destination,
            backup_dir,
            name="lifecycle",
            expected_sha256=source_hash,
            apply=True,
            confirmation="INSTALL",
        )
        self.assertEqual(applied["status"], "installed")
        self.assertEqual(release.sha256_file(destination), source_hash)
        self.assertEqual(stat.S_IMODE(backup_dir.stat().st_mode), 0o700)
        backups = list(backup_dir.glob("*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)


class ProductionCanaryJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "canary"
        self.directory.mkdir(mode=0o700)
        os.chmod(self.directory, 0o700)

    def test_private_one_shot_journal_refuses_directory_reuse(self):
        journal = stress.prepare_canary_journal(self.directory, role="josh2")
        claim = self.directory / stress.CANARY_CLAIM_FILENAME
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(claim.stat().st_mode), 0o600)
        with self.assertRaises(RuntimeError):
            stress.prepare_canary_journal(self.directory, role="josh2")

    def test_journal_directory_must_be_caller_created_exact_0700(self):
        os.chmod(self.directory, 0o755)
        with self.assertRaises(RuntimeError):
            stress.prepare_canary_journal(self.directory, role="josh2")

    def test_journal_retains_exact_ids_until_cleanup_is_fully_reconciled(self):
        journal = stress.prepare_canary_journal(self.directory, role="josh2")
        stress.write_canary_journal(
            journal,
            stage="send-receipt",
            chat_id="private-chat-ref",
            thread_id="private-topic-ref",
            message_ids=["101", "102"],
            indeterminate_stages=[],
        )
        incomplete = {
            "attempted": 2,
            "deleted": 1,
            "failedIds": ["102"],
            "indeterminateIds": [],
            "indeterminateStages": [],
            "records": [
                {"messageId": "101", "deleted": True, "attempts": 1},
                {"messageId": "102", "deleted": False, "attempts": 3},
            ],
        }
        self.assertFalse(
            stress.finalize_canary_journal(
                journal,
                incomplete,
                "private-chat-ref",
                "private-topic-ref",
                ["101", "102"],
            )
        )
        pending = json.loads(journal.read_text(encoding="utf-8"))
        self.assertEqual(pending["stage"], "cleanup-pending")
        self.assertEqual(pending["messageIds"], ["101", "102"])

        complete = {
            "attempted": 2,
            "deleted": 2,
            "failedIds": [],
            "indeterminateIds": ["102"],
            "indeterminateStages": [],
            "records": [
                {"messageId": "101", "deleted": True, "attempts": 1},
                {"messageId": "102", "deleted": True, "attempts": 1},
            ],
        }
        self.assertTrue(
            stress.finalize_canary_journal(
                journal,
                complete,
                "private-chat-ref",
                "private-topic-ref",
                ["101", "102"],
            )
        )
        receipt = self.directory / stress.CANARY_RECEIPT_FILENAME
        confirmed = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(confirmed["stage"], "cleanup-confirmed")
        self.assertTrue(confirmed["cleanupConfirmed"])
        self.assertFalse(journal.exists())
        self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
        self.assertTrue((self.directory / stress.CANARY_CLAIM_FILENAME).exists())

    def test_live_canary_directory_defaults_from_required_environment_variable(self):
        with patch.dict(os.environ, {
            "TELEGRAM_CANARY_CLEANUP_JOURNAL": str(self.directory),
        }):
            with patch.object(stress, "load_module") as load:
                module = SimpleNamespace(
                    build_completion_summary=lambda **_kwargs: "<b>Complete:</b> done",
                )
                load.return_value = module
                with patch.object(stress, "validate", return_value=[]), patch.object(
                    stress, "render_stress", return_value={
                        "ok": True, "iterations": 1, "renderedCards": 1,
                        "milestoneSequences": [], "problems": [],
                    },
                ), patch.object(stress, "live_target_problems", return_value=[]), patch.object(
                    stress, "live_canary", return_value={"ok": True},
                ), patch.object(
                    os.sys, "argv", [
                        "stress", "--role", "josh2", "--live", "--iterations", "1",
                        "--confirm-production-canary", "--thread-id", "1",
                    ],
                ), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(stress.main(), 0)
        self.assertTrue((self.directory / stress.CANARY_JOURNAL_FILENAME).exists())

    def test_production_stdout_projection_contains_statuses_and_counts_not_ids_or_errors(self):
        private_id = "987654321"
        raw_error = "private transport detail"
        projected = stress.production_canary_stdout(
            {
                "role": "josh2",
                "ok": False,
                "problems": [raw_error],
                "stress": {
                    "ok": True,
                    "iterations": 20,
                    "renderedCards": 20,
                    "problems": [],
                },
                "transport": {
                    "ok": False,
                    "error": raw_error,
                    "failures": [raw_error],
                    "cleanupConfirmed": False,
                    "cleanup": {
                        "attempted": 1,
                        "deleted": 0,
                        "failedIds": [private_id],
                        "indeterminateIds": [],
                        "indeterminateStages": [],
                        "records": [{"messageId": private_id, "error": raw_error}],
                    },
                    "final": {"attempts": 1, "successes": 1, "messageIds": [private_id]},
                    "timing": {"cumulativeMs": {"final": 123.4}},
                },
            }
        )
        encoded = json.dumps(projected, sort_keys=True)
        self.assertNotIn(private_id, encoded)
        self.assertNotIn(raw_error, encoded)
        self.assertNotIn("messageIds", encoded)
        self.assertNotIn("records", encoded)
        self.assertNotIn("timing", encoded)
        self.assertEqual(projected["transport"]["cleanup"]["failedCount"], 1)
        self.assertEqual(projected["transport"]["final"]["count"], 1)

    def test_basic_live_canary_requires_one_separate_final_and_cleans_both_messages(self):
        journal = stress.prepare_canary_journal(self.directory, role="jaimes")
        calls: list[str] = []

        def send_card(*_args, **_kwargs):
            calls.append("card")
            return {"ok": True, "result": {"message_id": 101}}

        def edit_card(*_args, **_kwargs):
            calls.append("edit")
            return {"ok": True, "result": True}

        def send_final(*_args, **_kwargs):
            calls.append("final")
            return {"ok": True, "result": {"message_id": 102}}

        def api_call(method, *_args, **_kwargs):
            self.assertEqual(method, "deleteMessage")
            calls.append("delete")
            return {"ok": True, "result": True}

        module = SimpleNamespace(
            send_card=send_card,
            edit_card=edit_card,
            build_completion_summary=lambda **_kwargs: "structured final",
            send_final_summary=send_final,
            api_call=api_call,
        )
        with patch.object(stress, "validate", return_value=[]):
            result = stress.basic_live_canary(
                module,
                "-2000000000000",
                "17",
                journal,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["final"]["attempts"], 1)
        self.assertEqual(result["final"]["successes"], 1)
        self.assertEqual(len(result["final"]["messageIds"]), 1)
        self.assertTrue(result["final"]["exactlyOne"])
        self.assertEqual(result["cleanup"]["attempted"], 2)
        self.assertEqual(result["cleanup"]["deleted"], 2)
        self.assertEqual(calls.count("final"), 1)
        self.assertEqual(calls.count("delete"), 2)
        self.assertFalse(journal.exists())
        self.assertTrue((self.directory / stress.CANARY_RECEIPT_FILENAME).is_file())

    def test_live_cli_without_private_journal_refuses_before_transport(self):
        module = SimpleNamespace(build_completion_summary=lambda **_: "offline summary")
        stream = io.StringIO()
        with patch.object(
            stress.sys,
            "argv",
            [
                "telegram_response_contract_stress.py",
                "--role",
                "josh2",
                "--live",
                "--iterations",
                "1",
                "--chat-id",
                "-2000000000000",
                "--thread-id",
                "2",
            ],
        ), patch.object(stress, "load_module", return_value=module), patch.object(
            stress,
            "validate",
            return_value=[],
        ), patch.object(
            stress,
            "render_stress",
            return_value={
                "ok": True,
                "iterations": 1,
                "renderedCards": 1,
                "milestoneSequences": [],
                "problems": [],
            },
        ), patch.object(
            stress,
            "live_canary",
            side_effect=AssertionError("transport must not run"),
        ) as transport, contextlib.redirect_stdout(stream):
            code = stress.main()
        self.assertEqual(code, 1)
        transport.assert_not_called()
        output = json.loads(stream.getvalue())
        self.assertEqual(output["status"], "failed")
        self.assertEqual(output["transport"]["status"], "not-run")
        self.assertEqual(output["problemCount"], 1)


if __name__ == "__main__":
    unittest.main()
