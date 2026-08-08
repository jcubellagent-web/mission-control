from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_wrapper_module():
    path = ROOT / "scripts" / "cron_brain_feed_wrap.py"
    spec = importlib.util.spec_from_file_location("cron_brain_feed_wrap_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_publish_keeps_the_exact_work_lifecycle_identity() -> None:
    module = load_wrapper_module()
    with mock.patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
        module.publish(
            "jaimes", "active", "FCC monitor", "Monitor FCC", "Started", "Hermes",
            work_id="work-fcc", run_id="run-fcc", work_event="heartbeat", phase="executing",
            lease_seconds=180,
        )
    command = run.call_args.args[0]
    assert command[command.index("--work-id") + 1] == "work-fcc"
    assert command[command.index("--run-id") + 1] == "run-fcc"
    assert command[command.index("--work-event") + 1] == "heartbeat"
    assert command[command.index("--lease-seconds") + 1] == "180"
