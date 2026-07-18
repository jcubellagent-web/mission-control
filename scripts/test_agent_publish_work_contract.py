#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_publish
import agent_task


def publish_args(**overrides):
    values = {
        "work_id": "",
        "run_id": "",
        "generation": None,
        "sequence": None,
        "work_event": "auto",
        "status": "active",
        "title": "Implement live work identity",
        "tool": "Codex",
        "detail": "Writing the canonical work event",
        "phase": "implementation",
        "origin": "",
        "origin_claim": "",
        "origin_claim_hash": "",
        "model_family": "codex",
        "model_id": "gpt-5.6-sol",
        "route_verified": True,
        "clear_route": False,
        "lease_seconds": 180,
        "privacy": "dashboard-safe",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class AgentPublishWorkContractTests(unittest.TestCase):
    def fake_result(self, payload):
        return {
            "event": {
                "eventId": payload["event_id"],
                "workId": payload["work_id"],
                "runId": payload["run_id"],
                "generation": payload.get("generation") or 1,
                "sequence": payload.get("sequence") or 1,
            },
            "work": {
                "workId": payload["work_id"],
                "runId": payload["run_id"],
                "generation": payload.get("generation") or 1,
                "sequence": payload.get("sequence") or 1,
            },
        }

    def test_legacy_publish_becomes_explicit_ad_hoc_work(self) -> None:
        captured = {}

        def fake_publish(**payload):
            captured.update(payload)
            return self.fake_result(payload)

        with mock.patch.object(agent_publish, "publish_work_event", side_effect=fake_publish):
            agent_publish.publish_canonical_work(
                publish_args(),
                agent="joshex",
                now="2026-07-18T01:00:00Z",
                event_id_value="event-legacy",
            )
        self.assertTrue(captured["work_id"].startswith("work-adhoc-"))
        self.assertTrue(captured["run_id"].startswith("run-"))
        self.assertEqual(captured["kind"], "start")
        self.assertEqual(captured["origin"], "legacy-agent-publish")

    def test_explicit_identity_and_route_are_forwarded_unchanged(self) -> None:
        captured = {}

        def fake_publish(**payload):
            captured.update(payload)
            return self.fake_result(payload)

        args = publish_args(
            work_id="work-telegram-123",
            run_id="run-telegram-123",
            generation=4,
            sequence=7,
            work_event="heartbeat",
            origin="telegram-josh2",
            origin_claim_hash="a" * 64,
        )
        with mock.patch.object(agent_publish, "publish_work_event", side_effect=fake_publish):
            agent_publish.publish_canonical_work(
                args,
                agent="josh2",
                now="2026-07-18T01:00:00Z",
                event_id_value="event-explicit",
            )
        self.assertEqual(captured["work_id"], "work-telegram-123")
        self.assertEqual(captured["run_id"], "run-telegram-123")
        self.assertEqual(captured["generation"], 4)
        self.assertEqual(captured["sequence"], 7)
        self.assertEqual(captured["kind"], "heartbeat")
        self.assertEqual(captured["model_family"], "codex")
        self.assertEqual(captured["model_id"], "gpt-5.6-sol")
        self.assertTrue(captured["route_verified"])

    def test_agent_task_forwards_exact_work_identity(self) -> None:
        task = {
            "workId": "work-task-1",
            "runId": "run-task-1",
            "generation": 2,
            "origin": "telegram-jaimes",
            "originClaimHash": "b" * 64,
            "modelFamily": "antigravity",
            "modelId": "gemini-2.5-pro",
            "routeVerified": True,
        }
        completed = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        with mock.patch.object(agent_task.subprocess, "run", return_value=completed) as run:
            agent_task.publish_event(
                "jaimes",
                "status",
                "active",
                "Task active: inspect market signals",
                "Reviewing dashboard-safe inputs",
                True,
                task=task,
                phase="research",
                work_event="update",
            )
        command = run.call_args.args[0]
        self.assertIn("--work-id", command)
        self.assertEqual(command[command.index("--work-id") + 1], "work-task-1")
        self.assertEqual(command[command.index("--run-id") + 1], "run-task-1")
        self.assertEqual(command[command.index("--origin-claim-hash") + 1], "b" * 64)
        self.assertIn("--route-verified", command)

    def test_non_dashboard_safe_publish_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            agent_publish.ensure_safe("harmless", privacy="agent-private")

    def test_dashboard_text_redacts_internal_stack_details(self) -> None:
        traceback = 'Traceback (most recent call last):\n  File "/Users/operator/private.py", line 14'
        self.assertEqual(
            agent_publish.dashboard_text(traceback),
            "Internal runtime error; details remain in host-local logs.",
        )
        self.assertNotIn(
            "/Users/",
            agent_publish.dashboard_text("See /Users/operator/project/output.json for details"),
        )
        self.assertNotIn(
            "scripts/worker.py:41",
            agent_publish.dashboard_text("Failed at scripts/worker.py:41"),
        )

    def test_agent_task_cli_drives_one_exact_work_to_terminal(self) -> None:
        root = Path(agent_task.ROOT)
        with tempfile.TemporaryDirectory() as raw:
            data = Path(raw) / "data"
            environment = {
                **os.environ,
                "CONTROL_TOWER_DATA_DIR": str(data),
                "CONTROL_TOWER_WORK_DB": str(data / "work.db"),
                "CONTROL_TOWER_HOT_JSON": str(data / "hot.json"),
                "CONTROL_TOWER_HANDOFF_DIR": str(data / "handoffs"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            base = [sys.executable, "-B", str(root / "scripts" / "agent_task.py")]
            made = subprocess.run(
                base + [
                    "create",
                    "--id", "task-integration-1",
                    "--owner", "jaimes",
                    "--requester", "joshex",
                    "--title", "Integrate canonical work flow",
                    "--objective", "Verify exact lifecycle identity end to end",
                    "--model-family", "antigravity",
                    "--model-id", "gemini-2.5-pro",
                    "--route-verified",
                ],
                cwd=root,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            task = json.loads(made.stdout)["task"]
            for command in ("start", "heartbeat", "complete"):
                subprocess.run(
                    base + [command, "--id", task["id"], "--agent", "jaimes"],
                    cwd=root,
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            hot = json.loads((data / "hot.json").read_text())
            self.assertEqual(hot["revision"], 5)
            self.assertEqual(hot["activeWorks"], [])
            self.assertEqual(hot["activeModelRoutes"], [])
            self.assertEqual(hot["works"][0]["workId"], "task-integration-1")
            self.assertEqual(hot["works"][0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
