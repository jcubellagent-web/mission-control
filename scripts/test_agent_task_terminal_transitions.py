from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import agent_task


class AgentTaskTerminalTransitionTests(unittest.TestCase):
    def test_shared_source_task_cannot_complete_without_closeout_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-source-closeout-") as directory:
            root = Path(directory)
            task_path = root / "agent-task-queue.json"
            task_path.write_text(json.dumps({
                "tasks": [{
                    "id": "task-source-1", "workId": "work-source-1", "runId": "run-source-1",
                    "generation": 1, "origin": "test", "originClaimHash": "b" * 64,
                    "title": "Source closeout", "objective": "Close source", "owner": "joshex",
                    "requester": "joshex", "status": "active", "workScope": "shared-source",
                    "sourceClosure": {"version": 1, "status": "pending"},
                    "artifactContract": {"version": 1, "required": True, "status": "pending"},
                    "artifacts": [], "notes": [],
                }],
            }), encoding="utf-8")
            before = task_path.read_bytes()
            args = SimpleNamespace(
                agent="joshex", id="task-source-1", work_event="terminal", owner="",
                artifact=[], summary="", note="done", phase="", model_family=None,
                model_id=None, route_verified=None, artifact_outcome="no-artifact-needed",
                artifact_reason="No durable artifact", no_brain_feed=True, job=False,
                lease_seconds=900, cmd="complete",
            )
            with mock.patch.object(agent_task, "TASKS_PATH", task_path), \
                 mock.patch.object(agent_task, "SOURCE_STATE_DIR", root / "state"), \
                 mock.patch.object(agent_task, "SOURCE_LEASE_PATH", root / "state" / "lease.json"), \
                 mock.patch.object(agent_task, "SOURCE_CLOSEOUT_DIR", root / "state" / "receipts"), \
                 mock.patch.object(agent_task, "SOURCE_LIFECYCLE_LOCK_PATH", root / "state" / "lifecycle.lock"), \
                 mock.patch.object(agent_task, "publish_event") as publish, \
                 self.assertRaisesRegex(SystemExit, "closeout evidence is missing"):
                agent_task.set_status(args, "done")

            self.assertEqual(before, task_path.read_bytes())
            publish.assert_not_called()

    def test_artifact_contract_requires_explicit_closeout(self) -> None:
        task = {"artifactContract": {"version": 1, "required": True, "status": "pending"}, "artifacts": []}
        args = SimpleNamespace(artifact_outcome="", artifact_reason="")
        with self.assertRaisesRegex(SystemExit, "artifact-outcome"):
            agent_task.artifact_decision(task, args, "joshex", "2026-08-02T00:00:00Z")

    def test_promoted_artifact_is_bound_to_governed_memory_proposal(self) -> None:
        task = {
            "artifactContract": {"version": 1, "required": True, "status": "pending"},
            "artifacts": ["docs/shared-contract.md"],
        }
        args = SimpleNamespace(artifact_outcome="promoted", artifact_reason="Reusable across projects")
        decision = agent_task.artifact_decision(task, args, "joshex", "2026-08-02T00:00:00Z")
        self.assertEqual("pending-proposal", decision["memoryStatus"])
        self.assertEqual("satisfied", task["artifactContract"]["status"])

    def test_legacy_task_remains_compatible_but_records_default(self) -> None:
        task = {"artifacts": []}
        args = SimpleNamespace(artifact_outcome="", artifact_reason="")
        decision = agent_task.artifact_decision(task, args, "joshex", "2026-08-02T00:00:00Z")
        self.assertEqual("no-artifact-needed", decision["outcome"])
        self.assertEqual("legacy-default", decision["decisionSource"])

    def test_artifact_memory_proposal_binds_task_work_and_run(self) -> None:
        task = {
            "id": "task-a", "workId": "work-a", "runId": "run-a",
            "title": "Reusable result", "objective": "Create it", "owner": "joshex",
            "privacy": "dashboard-safe", "artifacts": ["docs/result.md"],
            "artifactDecision": {
                "outcome": "promoted", "reason": "Used by other projects",
                "artifacts": ["docs/result.md"],
            },
        }
        completed = mock.Mock(returncode=0, stdout='{"id":"candidate-a","status":"candidate"}', stderr="")
        with mock.patch.object(agent_task.subprocess, "run", return_value=completed) as run:
            result = agent_task.propose_artifact_memory(task, "joshex")
        self.assertEqual("candidate-a", result["candidateId"])
        command = run.call_args.args[0]
        self.assertEqual("task-a|work-a|run-a", command[command.index("--source-ref") + 1])

    def test_registry_failure_is_returned_for_durable_task_ledger_replay(self) -> None:
        task = {
            "id": "task-a", "workId": "work-a", "runId": "run-a",
            "title": "Reusable result", "objective": "Create it", "owner": "joshex",
            "privacy": "dashboard-safe", "artifacts": ["docs/result.md"],
            "artifactDecision": {"outcome": "promoted", "reason": "Reusable", "artifacts": ["docs/result.md"]},
        }
        completed = mock.Mock(returncode=1, stdout="", stderr="registry unavailable")
        with mock.patch.object(agent_task.subprocess, "run", return_value=completed):
            result = agent_task.propose_artifact_memory(task, "joshex")
        self.assertEqual("proposal-error", result["status"])
        self.assertIn("registry unavailable", result["error"])

    def test_conflicting_terminal_transition_fails_before_any_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-task-terminal-") as directory:
            task_path = Path(directory) / "agent-task-queue.json"
            task_path.write_text(json.dumps({
                "updatedAt": "2026-07-18T21:39:38Z",
                "tasks": [{
                    "id": "task-terminal-1",
                    "workId": "work-terminal-1",
                    "runId": "run-terminal-1",
                    "generation": 1,
                    "origin": "test",
                    "originClaimHash": "a" * 64,
                    "title": "Terminal transition guard",
                    "objective": "Remain immutable until explicitly reopened",
                    "owner": "jaimes",
                    "requester": "joshex",
                    "status": "blocked",
                    "notes": [],
                }],
            }, indent=2) + "\n", encoding="utf-8")
            before = task_path.read_bytes()
            args = SimpleNamespace(
                agent="joshex",
                id="task-terminal-1",
                work_event="update",
            )

            with mock.patch.object(agent_task, "TASKS_PATH", task_path), \
                 mock.patch.object(agent_task, "publish_event") as publish, \
                 self.assertRaisesRegex(SystemExit, "already terminal as blocked"):
                agent_task.set_status(args, "cancelled")

            self.assertEqual(before, task_path.read_bytes())
            publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
