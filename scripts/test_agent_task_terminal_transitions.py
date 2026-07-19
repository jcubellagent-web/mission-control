from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import agent_task


class AgentTaskTerminalTransitionTests(unittest.TestCase):
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
