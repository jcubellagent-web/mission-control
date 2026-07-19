import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "linear_work_intent.py"
SPEC = importlib.util.spec_from_file_location("linear_work_intent", MODULE_PATH)
assert SPEC and SPEC.loader
linear = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(linear)


CONFIG = {
    "enabled": True,
    "workspace": {
        "teamName": "Jcubellagent",
        "projectName": "Agent Ecosystem",
        "teamKey": "JCU",
        "teamId": "7767e152-10d8-4cb7-a353-76837480aafe",
        "projectId": "7f7e8df4-50b6-4585-8aad-ff2e0ddc6d62",
    },
    "intentBridge": {
        "enabled": True,
        "path": "data/linear-work-intents.json",
        "canonicalHost": "josh2",
        "canonicalSshTarget": "josh2.0@josh2",
        "canonicalRoot": "/Users/josh2.0/.openclaw/workspace/mission-control",
        "canonicalPython": "/opt/homebrew/bin/python3",
    },
    "connector": {
        "directAgents": ["joshex", "josh2", "jaimes"],
        "delegatedAgents": {"jain": "jaimes"},
    },
    "labels": {
        "agent": {
            "joshex": "JOSHeX",
            "josh2": "Josh 2.0",
            "jaimes": "JAIMES",
            "jain": "J.A.I.N",
        },
        "area": ["Control Tower", "Telegram", "Memory", "Automation", "Integrations", "Reliability"],
    },
    "statusMapping": {
        "accepted": "Todo",
        "planned": "Todo",
        "routed": "Todo",
        "active": "In Progress",
        "blocked": "In Progress",
        "verifying": "In Review",
        "done": "Done",
        "cancelled": "Canceled",
    },
}


def make_task(owner="joshex", status="queued"):
    return {
        "id": "task-durable-1",
        "workId": "work-durable-1",
        "generation": 1,
        "title": "Improve durable coordination",
        "objective": "Make durable work visible across agent lanes.",
        "owner": owner,
        "status": status,
        "priority": "high",
        "privacy": "dashboard-safe",
        "approval": "approved",
        "linear": linear.linear_metadata(
            area="Integrations",
            acceptance_criteria=["One issue is reused across handoffs."],
            config=CONFIG,
        ),
    }


@pytest.mark.parametrize(
    ("owner", "agent_label", "route_to"),
    [
        ("joshex", "JOSHeX", "joshex"),
        ("josh", "Josh 2.0", "josh2"),
        ("jaimes", "JAIMES", "jaimes"),
        ("jain", "J.A.I.N", "jaimes"),
    ],
)
def test_all_agent_routes_are_explicit(owner, agent_label, route_to):
    intent = linear.build_intent(make_task(owner), config=CONFIG)
    assert intent["agentLabel"] == agent_label
    assert intent["areaLabel"] == "Integrations"
    assert intent["labels"] == [agent_label, "Integrations"]
    assert intent["routeTo"] == route_to


def test_intents_are_idempotent_and_latest_boundary_wins(tmp_path):
    path = tmp_path / "intents.json"
    task = make_task()
    first = linear.enqueue_task_intent(task, path=path, config=CONFIG)
    repeated = linear.enqueue_task_intent(task, path=path, config=CONFIG)
    assert first["id"] == repeated["id"]
    assert len(json.loads(path.read_text())["intents"]) == 1

    task["status"] = "active"
    task["linear"]["revision"] = 2
    latest = linear.enqueue_task_intent(task, path=path, config=CONFIG)
    rows = json.loads(path.read_text())["intents"]
    assert latest["state"] == "In Progress"
    assert len(rows) == 2
    assert next(row for row in rows if row["id"] == first["id"])["syncState"] == "superseded"


def test_ack_persists_one_stable_issue_reference(tmp_path):
    outbox = tmp_path / "intents.json"
    tasks = tmp_path / "tasks.json"
    task = make_task("jain")
    intent = linear.enqueue_task_intent(task, path=outbox, config=CONFIG)
    tasks.write_text(json.dumps({"tasks": [task]}))
    claim = linear.claim_intent(intent["id"], consumer="jaimes", path=outbox, config=CONFIG)
    result = linear.update_intent_result(
        intent["id"],
        claim_token=claim["claimToken"],
        verified_work_id=intent["workId"],
        issue_id="JCU-42",
        path=outbox,
        tasks_path=tasks,
        config=CONFIG,
    )
    saved = json.loads(tasks.read_text())["tasks"][0]["linear"]
    assert result["syncState"] == "synced"
    assert saved["issueId"] == "JCU-42"
    assert saved["syncState"] == "synced"


def test_private_or_credential_like_content_is_rejected():
    for unsafe in [
        "password=not-safe",
        "token=supersecretvalue12345",
        "credential=not-safe",
        "api_key=not-safe",
        "secret=not-safe",
    ]:
        task = make_task()
        task["objective"] = f"Use {unsafe} in the connector."
        with pytest.raises(SystemExit, match="private or credential-like"):
            linear.build_intent(task, config=CONFIG)
    task = make_task()
    task["privacy"] = "agent-private"
    with pytest.raises(SystemExit, match="dashboard-safe"):
        linear.build_intent(task, config=CONFIG)


def test_stale_generation_cannot_move_work_backward(tmp_path):
    outbox = tmp_path / "intents.json"
    newest = make_task(status="done")
    newest["generation"] = 2
    latest = linear.enqueue_task_intent(newest, path=outbox, config=CONFIG)
    stale = make_task(status="active")
    stale["linear"]["revision"] = 99
    rejected = linear.enqueue_task_intent(stale, path=outbox, config=CONFIG)
    assert latest["generation"] == 2
    assert rejected["syncState"] == "rejected_stale"
    pending = linear.pending_intents(path=outbox, config=CONFIG)
    assert [row["id"] for row in pending] == [latest["id"]]


def test_claim_prevents_competing_or_late_results(tmp_path):
    outbox = tmp_path / "intents.json"
    intent = linear.enqueue_task_intent(make_task(), path=outbox, config=CONFIG)
    claim = linear.claim_intent(intent["id"], consumer="joshex", path=outbox, config=CONFIG)
    with pytest.raises(SystemExit, match="already claimed"):
        linear.claim_intent(intent["id"], consumer="josh2", path=outbox, config=CONFIG)
    synced = linear.update_intent_result(
        intent["id"],
        claim_token=claim["claimToken"],
        verified_work_id=intent["workId"],
        issue_id="JCU-77",
        path=outbox,
        tasks_path=tmp_path / "missing-tasks.json",
        config=CONFIG,
    )
    assert synced["syncState"] == "synced"
    with pytest.raises(SystemExit, match="cannot be changed"):
        linear.update_intent_result(
            intent["id"],
            claim_token=claim["claimToken"],
            verified_work_id=intent["workId"],
            error_code="late_failure",
            path=outbox,
            tasks_path=tmp_path / "missing-tasks.json",
            config=CONFIG,
        )


def test_claim_must_use_the_configured_connector_lane(tmp_path):
    outbox = tmp_path / "intents.json"
    intent = linear.enqueue_task_intent(make_task("jain"), path=outbox, config=CONFIG)
    with pytest.raises(SystemExit, match="routed to 'jaimes'"):
        linear.claim_intent(intent["id"], consumer="joshex", path=outbox, config=CONFIG)


def test_superseded_claim_and_wrong_team_issue_are_rejected(tmp_path):
    outbox = tmp_path / "intents.json"
    task = make_task()
    first = linear.enqueue_task_intent(task, path=outbox, config=CONFIG)
    claim = linear.claim_intent(first["id"], consumer="joshex", path=outbox, config=CONFIG)
    task["status"] = "active"
    task["linear"]["revision"] = 2
    linear.enqueue_task_intent(task, path=outbox, config=CONFIG)
    with pytest.raises(SystemExit, match="superseded"):
        linear.update_intent_result(
            first["id"],
            claim_token=claim["claimToken"],
            verified_work_id=first["workId"],
            issue_id="JCU-88",
            path=outbox,
            tasks_path=tmp_path / "missing-tasks.json",
            config=CONFIG,
        )

    latest = linear.pending_intents(path=outbox, config=CONFIG)[0]
    latest_claim = linear.claim_intent(latest["id"], consumer="joshex", path=outbox, config=CONFIG)
    with pytest.raises(SystemExit, match="configured JCU"):
        linear.update_intent_result(
            latest["id"],
            claim_token=latest_claim["claimToken"],
            verified_work_id=latest["workId"],
            issue_id="OTHER-1",
            path=outbox,
            tasks_path=tmp_path / "missing-tasks.json",
            config=CONFIG,
        )


def test_runtime_config_disable_path_and_outbox_integrity(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    disabled = dict(CONFIG)
    disabled["intentBridge"] = dict(CONFIG["intentBridge"], enabled=False)
    config_path.write_text(json.dumps(disabled))
    with pytest.raises(SystemExit, match="disabled"):
        linear.load_config(config_path)

    override = tmp_path / "custom-intents.json"
    monkeypatch.setenv("CONTROL_TOWER_LINEAR_INTENTS_PATH", str(override))
    assert linear.intent_path(CONFIG) == override
    override.write_text("{not-json")
    with pytest.raises(SystemExit, match="corrupt"):
        linear.pending_intents(path=override, config=CONFIG)


def test_pruning_never_drops_unresolved_work():
    unresolved = [
        {"id": "pending-1", "syncState": "pending"},
        {"id": "failed-1", "syncState": "failed"},
    ]
    terminal = [
        {"id": f"done-{index}", "syncState": "synced", "updatedAt": f"2026-01-01T00:{index % 60:02d}:00Z"}
        for index in range(700)
    ]
    kept = linear._prune_rows(terminal + unresolved)
    assert {row["id"] for row in unresolved}.issubset({row["id"] for row in kept})
    assert len(kept) == 502


def test_noncanonical_enqueue_forwards_to_the_canonical_outbox(monkeypatch):
    task = make_task("jain")
    observed = {}
    monkeypatch.setattr(linear, "is_canonical_runtime", lambda config: False)

    def submit(intent, *, config):
        observed["intent"] = intent
        return intent

    monkeypatch.setattr(linear, "_submit_to_canonical", submit)
    result = linear.enqueue_task_intent(task, config=CONFIG)
    assert observed["intent"]["workId"] == "work-durable-1"
    assert result["routeTo"] == "jaimes"
    assert task["linear"]["lastIntentId"] == result["id"]


def test_noncanonical_ingest_failure_is_fail_open_and_retryable(tmp_path, monkeypatch):
    outbox = tmp_path / "fallback-intents.json"
    monkeypatch.setenv("CONTROL_TOWER_LINEAR_INTENTS_PATH", str(outbox))
    monkeypatch.setattr(linear, "is_canonical_runtime", lambda config: False)
    monkeypatch.setattr(
        linear,
        "_submit_to_canonical",
        lambda intent, *, config: (_ for _ in ()).throw(SystemExit("offline")),
    )
    task = make_task("jaimes")
    result = linear.enqueue_task_intent(task, config=CONFIG)
    assert result["syncState"] == "failed"
    assert result["lastError"] == "canonical_unavailable"
    assert linear.pending_intents(path=outbox, config=CONFIG)[0]["id"] == result["id"]


def test_library_enqueue_honors_local_only_mode(tmp_path, monkeypatch):
    outbox = tmp_path / "local-intents.json"
    monkeypatch.setenv("CONTROL_TOWER_LINEAR_LOCAL_ONLY", "1")
    monkeypatch.setenv("CONTROL_TOWER_LINEAR_INTENTS_PATH", str(outbox))
    monkeypatch.setattr(linear, "is_canonical_runtime", lambda config: False)
    monkeypatch.setattr(
        linear,
        "_submit_to_canonical",
        lambda intent, *, config: (_ for _ in ()).throw(AssertionError("SSH must not run")),
    )
    result = linear.enqueue_task_intent(make_task("jain"), config=CONFIG)
    assert result["syncState"] == "pending"
    assert json.loads(outbox.read_text())["intents"][0]["id"] == result["id"]


def test_connector_result_publishes_a_terminal_receipt(tmp_path, monkeypatch):
    tasks = tmp_path / "agent-task-queue.json"
    source = make_task("jain")
    intent = linear.build_intent(source, config=CONFIG)
    source["linear"]["lastIntentId"] = intent["id"]
    connector = {
        "id": "task-linear-connector-test",
        "workId": "work-linear-connector-test",
        "runId": "run-linear-connector-test",
        "generation": 1,
        "origin": "linear-intent-delegation",
        "originClaimHash": "a" * 64,
        "owner": "jaimes",
        "status": "queued",
        "title": "Sync J.A.I.N durable work to Linear",
        "summary": "",
        "notes": [],
        "linearConnector": {
            "sourceTaskId": source["id"],
            "latestIntentId": intent["id"],
        },
    }
    tasks.write_text(json.dumps({"tasks": [source, connector]}))
    published = []
    monkeypatch.setattr(
        linear,
        "_publish_connector_terminal",
        lambda task, *, sync_state: published.append((task["id"], sync_state)),
    )
    linear._update_task_after_result(
        task_id=source["id"],
        intent_id=intent["id"],
        issue_id="JCU-77",
        sync_state="synced",
        error_code=None,
        tasks_path=tasks,
    )
    saved = json.loads(tasks.read_text())["tasks"]
    saved_connector = next(row for row in saved if row["id"] == connector["id"])
    assert published == [(connector["id"], "synced")]
    assert saved_connector["status"] == "done"
    assert saved_connector["linearConnector"]["terminalPublishState"] == "done"


def test_flush_local_replays_the_latest_failed_boundary(tmp_path, monkeypatch):
    outbox = tmp_path / "fallback-intents.json"
    monkeypatch.setenv("CONTROL_TOWER_LINEAR_INTENTS_PATH", str(outbox))
    monkeypatch.setattr(linear, "is_canonical_runtime", lambda config: False)
    task = make_task("jaimes")
    failed = linear.build_intent(task, config=CONFIG)
    failed["syncState"] = "failed"
    failed["lastError"] = "canonical_unavailable"
    linear._enqueue_intent_local(failed, path=outbox, config=CONFIG)
    observed = []

    def submit(intent, *, config):
        observed.append(intent)
        returned = dict(intent)
        returned["syncState"] = "pending"
        return returned

    monkeypatch.setattr(linear, "_submit_to_canonical", submit)
    result = linear.flush_local_intents(path=outbox, config=CONFIG)
    assert result == {"ok": True, "attempted": 1, "forwarded": 1, "failed": 0}
    assert observed[0]["syncState"] == "pending"
    saved = json.loads(outbox.read_text())["intents"][0]
    assert saved["syncState"] == "forwarded"
    assert saved["canonicalSyncState"] == "pending"


def test_flush_local_keeps_a_failed_row_retryable(tmp_path, monkeypatch):
    outbox = tmp_path / "fallback-intents.json"
    monkeypatch.setattr(linear, "is_canonical_runtime", lambda config: False)
    failed = linear.build_intent(make_task("jain"), config=CONFIG)
    failed["syncState"] = "failed"
    failed["lastError"] = "canonical_unavailable"
    linear._enqueue_intent_local(failed, path=outbox, config=CONFIG)
    monkeypatch.setattr(
        linear,
        "_submit_to_canonical",
        lambda intent, *, config: (_ for _ in ()).throw(SystemExit("still offline")),
    )
    result = linear.flush_local_intents(path=outbox, config=CONFIG)
    assert result == {"ok": False, "attempted": 1, "forwarded": 0, "failed": 1}
    saved = json.loads(outbox.read_text())["intents"][0]
    assert saved["syncState"] == "failed"
    assert saved["lastError"] == "canonical_unavailable"
    assert saved["attempts"] == 1


def test_cross_host_ssh_target_does_not_require_an_alias(monkeypatch):
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        return type("Result", (), {"returncode": 0, "stdout": json.dumps({"ok": True, "intent": {"id": "ok"}}), "stderr": ""})()

    monkeypatch.setattr(linear.subprocess, "run", run)
    linear._submit_to_canonical(make_task(), config=CONFIG)
    assert observed["command"][:6] == [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "josh2.0@josh2",
    ]
