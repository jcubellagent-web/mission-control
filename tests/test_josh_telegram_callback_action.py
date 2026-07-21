from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "josh_telegram_callback_action.py"
sys.path.insert(0, str(ROOT / "scripts"))

from telegram_gateway_lifecycle import GatewayLifecycle, RolloutPolicy  # noqa: E402


class JoshTelegramCallbackActionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="telegram-callback-test-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.lifecycle_root = self.folder / "lifecycle"

    def write_rollout(self, state: str) -> Path:
        path = self.folder / f"rollout-{state}.json"
        path.write_text(
            json.dumps(
                {
                    "masterState": state,
                    "globalKillSwitch": False,
                    "brainKillSwitch": True,
                    "hosts": {"josh2": True, "jaimes": True},
                    "writerLifecycleVersion": 3,
                    "readerLifecycleVersions": [2, 3],
                    "shadowMinimumPerOwner": 20,
                    "brainFixtureMinimum": 20,
                }
            ),
            encoding="utf-8",
        )
        return path

    def run_cli(self, action: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), action, *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def issue_action(self) -> tuple[str, dict[str, str]]:
        lifecycle = GatewayLifecycle(
            self.lifecycle_root,
            # Issue under v3 authority, then exercise the callback after the
            # CLI rolls new work back to legacy/off. The active receipt stays
            # pinned and drains through its original writer.
            rollout=RolloutPolicy(master_state="josh2", host_enabled={"josh2": True}),
            owner="josh2",
        )
        receipt = lifecycle.start_work(
            origin_key="callback-fixture-origin",
            run_id="callback-fixture-run",
            intake_agent="josh2",
            current_owner="josh2",
            surface_contract="telegram",
            text="Forget the fixture after confirmation",
            classification=(3, "approval"),
        )
        bindings = {
            "authorized_user": "authorized-user-ref",
            "chat_ref": "chat-ref",
            "topic_ref": "topic-ref",
            "message_ref": "message-ref",
            "artifact_ref": "artifact-ref",
        }
        token = lifecycle.create_action(
            work_id=receipt["workId"],
            lifecycle_revision=receipt["sequence"],
            action="forget-confirm",
            **bindings,
        )
        return token, bindings

    def binding_args(self, bindings: dict[str, str], rollout: Path) -> list[str]:
        return [
            "--authorized-user", bindings["authorized_user"],
            "--chat-ref", bindings["chat_ref"],
            "--topic-ref", bindings["topic_ref"],
            "--message-ref", bindings["message_ref"],
            "--artifact-ref", bindings["artifact_ref"],
            "--lifecycle-root", str(self.lifecycle_root),
            "--rollout", str(rollout),
        ]

    def test_v3_callback_validates_every_binding_and_consumes_once_across_processes(self) -> None:
        token, bindings = self.issue_action()
        rollout = self.write_rollout("off")
        for binding in bindings:
            with self.subTest(binding=binding):
                wrong = dict(bindings)
                wrong[binding] = f"wrong-{binding}"
                rejected = self.run_cli(token, *self.binding_args(wrong, rollout))
                self.assertEqual(rejected.returncode, 3)
                self.assertEqual(json.loads(rejected.stdout)["status"], "rejected")

        accepted = self.run_cli(token, *self.binding_args(bindings, rollout))
        self.assertEqual(accepted.returncode, 0, accepted.stderr or accepted.stdout)
        payload = json.loads(accepted.stdout)
        self.assertEqual(payload["status"], "accepted")
        self.assertEqual(payload["action"], "forget-confirm")
        self.assertEqual(payload["artifactRef"], bindings["artifact_ref"])
        self.assertTrue(payload["executeAsync"])
        self.assertNotIn("buttons", payload)

        replay = self.run_cli(token, *self.binding_args(bindings, rollout))
        self.assertEqual(replay.returncode, 3)
        self.assertEqual(json.loads(replay.stdout)["errorClass"], "action-already-consumed")

    def test_v3_altered_token_has_no_effect_and_original_remains_usable(self) -> None:
        token, bindings = self.issue_action()
        rollout = self.write_rollout("off")
        prefix, secret, nonce = token.split(".")
        altered = ".".join((prefix, secret, f"{nonce}x"))

        rejected = self.run_cli(altered, *self.binding_args(bindings, rollout))
        self.assertEqual(rejected.returncode, 3)
        self.assertEqual(json.loads(rejected.stdout)["status"], "rejected")

        accepted = self.run_cli(token, *self.binding_args(bindings, rollout))
        self.assertEqual(accepted.returncode, 0, accepted.stderr or accepted.stdout)
        self.assertEqual(json.loads(accepted.stdout)["status"], "accepted")

    def test_legacy_callbacks_remain_available_when_off_or_shadow(self) -> None:
        for state in ("off", "shadow"):
            with self.subTest(state=state):
                rollout = self.write_rollout(state)
                process = self.run_cli(
                    "next:hold", "--dry-run", "--rollout", str(rollout)
                )
                self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
                self.assertIn("Status: paused", process.stdout)
                self.assertNotIn("legacy-callback-disabled", process.stdout)

    def test_legacy_callback_is_rejected_once_the_v3_writer_is_enabled(self) -> None:
        rollout = self.write_rollout("josh2")
        process = self.run_cli("next:hold", "--dry-run", "--rollout", str(rollout))
        self.assertEqual(process.returncode, 3)
        payload = json.loads(process.stdout)
        self.assertEqual(payload, {
            "ok": False,
            "status": "rejected",
            "errorClass": "legacy-callback-disabled",
        })

    def test_malformed_v3_callback_is_rejected_without_legacy_dispatch(self) -> None:
        rollout = self.write_rollout("off")
        process = self.run_cli(
            "v3.not-a-valid-action", "--dry-run", "--rollout", str(rollout),
            "--lifecycle-root", str(self.lifecycle_root),
        )
        self.assertEqual(process.returncode, 3)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["errorClass"], "malformed-action-token")


if __name__ == "__main__":
    unittest.main()
