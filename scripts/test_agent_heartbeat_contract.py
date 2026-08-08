#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_heartbeat


class AgentHeartbeatContractTests(unittest.TestCase):
    def test_brain_feed_heartbeat_does_not_create_a_work_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            heartbeats = Path(raw) / "agent-heartbeats.json"
            args = argparse.Namespace(
                agent="jaimes",
                node="jaimes-live-poller",
                status="active",
                summary="Availability check only",
                stale_after=120,
                brain_feed=True,
                v2=True,
            )
            with mock.patch.object(agent_heartbeat, "HEARTBEATS_PATH", heartbeats):
                record = agent_heartbeat.write_heartbeat(args)
            payload = json.loads(heartbeats.read_text(encoding="utf-8"))

        self.assertEqual(record["status"], "active")
        self.assertEqual(payload["heartbeats"][0]["node"], "jaimes-live-poller")
        self.assertNotIn("workId", payload["heartbeats"][0])


if __name__ == "__main__":
    unittest.main()
