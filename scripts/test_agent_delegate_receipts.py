#!/usr/bin/env python3
from __future__ import annotations

import json
from contextlib import ExitStack
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_delegate


class AgentDelegateReceiptTests(unittest.TestCase):
    def task(self):
        return {
            "id": "task-delegate-1",
            "workId": "work-delegate-1",
            "runId": "run-delegate-1",
            "generation": 1,
            "origin": "agent-delegate",
            "originClaimHash": "b" * 64,
            "modelFamily": None,
            "modelId": None,
            "routeVerified": False,
            "title": "Safe delegated task",
            "objective": "Safe delegated objective",
            "owner": "jaimes",
            "requester": "joshex",
        }

    def test_remote_receipt_is_exact_ack_evidence_not_a_second_ledger(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps({
            "ok": True,
            "receipt": {
                "kind": "acknowledged",
                "agent": "jaimes",
                "workId": "work-delegate-1",
                "runId": "run-delegate-1",
                "taskId": "task-delegate-1",
            },
        }), stderr="")
        with mock.patch.object(agent_delegate, "run", return_value=completed) as run:
            agent_delegate.publish_remote_receipt("jaimes", self.task())
        command = run.call_args.args[0]
        self.assertEqual(command[0], "ssh")
        remote_command = command[2]
        self.assertIn("scripts/handoff_receipt_bridge.py", remote_command)
        self.assertIn("acknowledge", remote_command)
        self.assertNotIn("scripts/agent_publish.py", remote_command)
        self.assertNotIn("control-tower-work.sqlite3", remote_command)

    def test_remote_receipt_rejects_mismatched_identity(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps({
            "ok": True,
            "receipt": {
                "kind": "acknowledged",
                "agent": "jaimes",
                "workId": "wrong-work",
                "runId": "run-delegate-1",
                "taskId": "task-delegate-1",
            },
        }), stderr="")
        with mock.patch.object(agent_delegate, "run", return_value=completed):
            with self.assertRaisesRegex(SystemExit, "exact handoff identity"):
                agent_delegate.publish_remote_receipt("jaimes", self.task())

    def test_sync_copies_task_and_handoff_context(self) -> None:
        remote = {"ssh": "jaimes", "path": "/remote/mission-control"}
        with tempfile.TemporaryDirectory() as temp_dir:
            task_path = Path(temp_dir) / "agent-task-queue.json"
            handoff_path = Path(temp_dir) / "handoff-queue.json"
            task_path.write_text("{}", encoding="utf-8")
            handoff_path.write_text("{}", encoding="utf-8")
            with mock.patch.object(agent_delegate, "TASK_QUEUE", task_path), \
                 mock.patch.object(agent_delegate, "HANDOFF_QUEUE", handoff_path), \
                 mock.patch.object(agent_delegate, "is_local_remote", return_value=False), \
                 mock.patch.object(agent_delegate, "run") as run:
                agent_delegate.sync_task_queue(remote)
        self.assertEqual(run.call_count, 2)
        destinations = [call.args[0][2] for call in run.call_args_list]
        self.assertEqual(destinations, [
            "jaimes:/remote/mission-control/data/agent-task-queue.json",
            "jaimes:/remote/mission-control/data/handoff-queue.json",
        ])

    def test_successful_delegate_adds_exact_acknowledgement_to_central_row(self) -> None:
        task = self.task()
        published = {
            "ok": True,
            "event": {"id": "event-central-ack", "time": "2026-07-18T16:01:00Z"},
        }
        argv = [
            "agent_delegate.py",
            "--to", "jaimes",
            "--requester", "joshex",
            "--title", "Safe delegated task",
            "--objective", "Safe delegated objective",
        ]
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, "argv", argv))
            stack.enter_context(mock.patch.object(agent_delegate, "create_task", return_value=task))
            stack.enter_context(mock.patch.object(agent_delegate, "sync_task_queue"))
            stack.enter_context(mock.patch.object(
                agent_delegate,
                "publish_remote_receipt",
                return_value=("Instruction received", "Safe receipt detail"),
            ))
            publish = stack.enter_context(
                mock.patch.object(agent_delegate, "publish", return_value=published)
            )
            receipt = stack.enter_context(
                mock.patch.object(agent_delegate, "record_receipt", return_value={"created": True})
            )
            stack.enter_context(mock.patch("builtins.print"))
            self.assertEqual(agent_delegate.main(), 0)
        self.assertEqual(publish.call_args.args[1], "status")
        kwargs = receipt.call_args.kwargs
        self.assertEqual(kwargs["kind"], "acknowledged")
        self.assertEqual(kwargs["agent"], "jaimes")
        self.assertEqual(kwargs["work_id"], "work-delegate-1")
        self.assertEqual(kwargs["run_id"], "run-delegate-1")
        self.assertEqual(kwargs["origin_claim_hash"], "b" * 64)
        self.assertEqual(kwargs["event_id"], "event-central-ack")
        self.assertEqual(kwargs["task_id"], "task-delegate-1")


if __name__ == "__main__":
    unittest.main()
