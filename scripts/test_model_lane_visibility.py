#!/usr/bin/env python3
from __future__ import annotations

import argparse
import unittest

import model_lane


def args(**overrides):
    values = {
        "transport": "auto",
        "requester": "joshex",
        "title": "Review work visibility",
        "controller_work_id": "work-parent",
        "controller_run_id": "run-parent",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ModelLaneVisibilityTests(unittest.TestCase):
    def test_remote_specialist_publishes_under_actual_jaimes_execution_lane(self) -> None:
        route = {"modelRoute": {"provider": "ollama", "model": "glm-5.2:cloud"}}
        command = model_lane.lane_publish_command(
            args(), route,
            work_id="work-worker",
            run_id="run-worker",
            work_event="start",
            status="active",
            phase="working",
            detail="Dashboard-safe worker state.",
        )
        self.assertEqual(command[command.index("--agent") + 1], "jaimes")
        self.assertEqual(command[command.index("--controller-work-id") + 1], "work-parent")

    def test_local_codex_lane_remains_under_requester(self) -> None:
        route = {"modelRoute": {"provider": "codex", "model": "gpt-5.6-terra"}}
        self.assertEqual(model_lane.execution_agent(args(), route), "joshex")


if __name__ == "__main__":
    unittest.main()
