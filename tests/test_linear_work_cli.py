import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_json(script: str, args: list[str], env: dict[str, str]) -> dict:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def isolated_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["CONTROL_TOWER_DATA_DIR"] = str(tmp_path / "data")
    env["CONTROL_TOWER_HANDOFF_DIR"] = str(tmp_path / "docs" / "handoffs")
    env["CONTROL_TOWER_LINEAR_INTENTS_PATH"] = str(tmp_path / "data" / "linear-work-intents.json")
    env["CONTROL_TOWER_LINEAR_LOCAL_ONLY"] = "1"
    return env


def test_agent_task_opt_in_lifecycle_and_handoff(tmp_path):
    env = isolated_env(tmp_path)
    outbox = tmp_path / "data" / "linear-work-intents.json"
    run_json(
        "agent_task.py",
        [
            "create", "--id", "task-local-only", "--owner", "joshex",
            "--title", "Routine local check", "--objective", "Run a same-session check.",
            "--privacy", "dashboard-safe", "--no-brain-feed",
        ],
        env,
    )
    assert not outbox.exists()

    created = run_json(
        "agent_task.py",
        [
            "create", "--id", "task-durable-cli", "--work-id", "work-durable-cli",
            "--owner", "joshex", "--title", "Durable CLI bridge",
            "--objective", "Verify the durable task lifecycle.", "--priority", "high",
            "--privacy", "dashboard-safe", "--approval", "approved", "--durable",
            "--area", "Integrations", "--acceptance-criterion", "One stable issue survives handoff.",
            "--no-brain-feed",
        ],
        env,
    )
    first_id = created["task"]["linear"]["lastIntentId"]
    assert created["task"]["linear"]["syncState"] == "pending"
    assert len(json.loads(outbox.read_text())["intents"]) == 1

    run_json(
        "agent_task.py",
        ["heartbeat", "--id", "task-durable-cli", "--agent", "joshex", "--note", "heartbeat", "--no-brain-feed"],
        env,
    )
    assert len(json.loads(outbox.read_text())["intents"]) == 1

    claim = run_json(
        "linear_work_intent.py",
        ["claim", "--intent-id", first_id, "--consumer", "joshex"],
        env,
    )["intent"]
    run_json(
        "linear_work_intent.py",
        [
            "ack", "--intent-id", first_id, "--claim-token", claim["claimToken"],
            "--issue-id", "JCU-123", "--verified-work-id", "work-durable-cli",
        ],
        env,
    )

    handed_off = run_json(
        "agent_task.py",
        [
            "handoff", "--id", "task-durable-cli", "--agent", "joshex", "--to", "jaimes",
            "--summary", "JAIMES owns verification.", "--no-brain-feed",
        ],
        env,
    )["task"]
    assert handed_off["owner"] == "jaimes"
    assert handed_off["linear"]["issueId"] == "JCU-123"

    verified = run_json(
        "agent_task.py",
        [
            "verify", "--id", "task-durable-cli", "--agent", "jaimes",
            "--summary", "Verification started.", "--no-brain-feed",
        ],
        env,
    )["task"]
    pending = run_json("linear_work_intent.py", ["pending", "--route-to", "jaimes"], env)["intents"]
    assert verified["linear"]["revision"] == 3
    assert len(pending) == 1
    assert pending[0]["agentLabel"] == "JAIMES"
    assert pending[0]["state"] == "In Review"
    assert pending[0]["issueId"] == "JCU-123"


def test_jain_delegation_creates_a_jaimes_connector_task(tmp_path):
    env = isolated_env(tmp_path)
    result = run_json(
        "agent_delegate.py",
        [
            "--to", "jain", "--requester", "joshex", "--title", "Durable J.A.I.N research",
            "--objective", "Run a durable dashboard-safe research task.", "--priority", "high",
            "--privacy", "dashboard-safe", "--approval", "approved", "--durable",
            "--area", "Integrations", "--acceptance-criterion", "The result is linked to one Linear issue.",
            "--no-remote-receipt",
        ],
        env,
    )
    assert result["task"]["owner"] == "jain"
    assert result["task"]["linear"]["routeTo"] == "jaimes"
    assert result["linearConnectorTask"]["owner"] == "jaimes"
    tasks = json.loads((tmp_path / "data" / "agent-task-queue.json").read_text())["tasks"]
    assert len(tasks) == 2
    assert sum(bool(task.get("linear", {}).get("durable")) for task in tasks) == 1

    connector_id = result["linearConnectorTask"]["id"]
    initial_intent = result["task"]["linear"]["lastIntentId"]
    run_json(
        "agent_task.py",
        ["heartbeat", "--id", result["task"]["id"], "--agent", "jain", "--no-brain-feed"],
        env,
    )
    tasks = json.loads((tmp_path / "data" / "agent-task-queue.json").read_text())["tasks"]
    connector = next(task for task in tasks if task["id"] == connector_id)
    assert connector["linearConnector"]["latestIntentId"] == initial_intent

    for command, expected_state in [
        ("start", "In Progress"),
        ("verify", "In Review"),
        ("complete", "Done"),
    ]:
        updated = run_json(
            "agent_task.py",
            [command, "--id", result["task"]["id"], "--agent", "jain", "--no-brain-feed"],
            env,
        )["task"]
        tasks = json.loads((tmp_path / "data" / "agent-task-queue.json").read_text())["tasks"]
        assert len(tasks) == 2
        connector = next(task for task in tasks if task["id"] == connector_id)
        assert connector["linearConnector"]["latestIntentId"] == updated["linear"]["lastIntentId"]
        pending = run_json("linear_work_intent.py", ["pending", "--route-to", "jaimes"], env)["intents"]
        assert pending[0]["state"] == expected_state

    claim = run_json(
        "linear_work_intent.py",
        ["claim", "--intent-id", pending[0]["id"], "--consumer", "jaimes"],
        env,
    )["intent"]
    run_json(
        "linear_work_intent.py",
        [
            "ack", "--intent-id", pending[0]["id"], "--claim-token", claim["claimToken"],
            "--issue-id", "JCU-456", "--verified-work-id", result["task"]["workId"],
        ],
        env,
    )
    tasks = json.loads((tmp_path / "data" / "agent-task-queue.json").read_text())["tasks"]
    connector = next(task for task in tasks if task["id"] == connector_id)
    assert connector["status"] == "done"
    assert connector["summary"] == "Linear issue JCU-456 synchronized."


def test_josh2_direct_lane_does_not_create_a_connector_task(tmp_path):
    env = isolated_env(tmp_path)
    created = run_json(
        "agent_task.py",
        [
            "create", "--id", "task-josh-direct", "--owner", "josh2",
            "--title", "Direct Josh 2.0 durable work", "--objective", "Track one direct issue.",
            "--privacy", "dashboard-safe", "--durable", "--area", "Reliability",
            "--acceptance-criterion", "One Josh 2.0 issue is linked.", "--no-brain-feed",
        ],
        env,
    )["task"]
    tasks = json.loads((tmp_path / "data" / "agent-task-queue.json").read_text())["tasks"]
    assert created["owner"] == "josh"
    assert created["linear"]["routeTo"] == "josh2"
    assert len(tasks) == 1
    assert all(not task.get("linearConnector") for task in tasks)
