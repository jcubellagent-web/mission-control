from __future__ import annotations

import json
import os
import signal
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from scripts import interaction_session_engine as engine


def write_config(path: Path, *, attempts: int = 2) -> None:
    path.write_text(
        json.dumps(
            {
                "personalMacFallback": {
                    "personalHost": "joshex",
                    "defaultVisibleHost": "josh2",
                    "backgroundHost": "jaimes",
                    "requireExplicitAcknowledgement": True,
                    "allowedReasons": ["oauth"],
                },
                "sessionEngine": {
                    "enabled": True,
                    "maxAttempts": attempts,
                    "verificationRequired": True,
                    "commandTimeoutSeconds": 5,
                    "operatorPollMilliseconds": 20,
                    "terminateGraceSeconds": 0.2,
                    "promotionReasons": ["semantic-miss", "verification-failed", "driver-down", "visual-state-required"],
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def paths(tmp_path: Path) -> dict[str, Path]:
    values = {
        "config": tmp_path / "config.json",
        "state": tmp_path / "sessions",
        "receipts": tmp_path / "receipts.jsonl",
        "control": tmp_path / "control.json",
    }
    write_config(values["config"])
    return values


def begin(paths: dict[str, Path]) -> dict:
    return engine.begin_session(
        owner="jaimes",
        target_host="jaimes",
        surface="browser-dom",
        intent="click",
        config_path=paths["config"],
        state_root=paths["state"],
        receipt_path=paths["receipts"],
        control_path=paths["control"],
    )


def test_verified_action_persists_tokens_only_locally(paths: dict[str, Path]) -> None:
    session = begin(paths)
    session = engine.observe(session, b"private before page text", phase="before", state_root=paths["state"], receipt_path=paths["receipts"])
    session = engine.start_attempt(session, state_root=paths["state"], receipt_path=paths["receipts"])
    session, verified = engine.verify(session, b"private after page text", state_root=paths["state"], receipt_path=paths["receipts"])
    assert verified is True
    assert session["state"] == "complete"
    receipt_text = paths["receipts"].read_text(encoding="utf-8")
    assert "private before" not in receipt_text
    assert "private after" not in receipt_text
    assert "Token" not in receipt_text


def test_verification_retries_are_bounded(paths: dict[str, Path]) -> None:
    session = begin(paths)
    for expected_state in ("recovering", "escalated"):
        session = engine.observe(session, b"same", phase="before", state_root=paths["state"], receipt_path=paths["receipts"])
        session = engine.start_attempt(session, state_root=paths["state"], receipt_path=paths["receipts"])
        session, verified = engine.verify(session, b"same", state_root=paths["state"], receipt_path=paths["receipts"])
        assert verified is False
        assert session["state"] == expected_state
    assert session["attempt"] == 2


def test_pause_is_fail_closed_and_resume_is_explicit(paths: dict[str, Path]) -> None:
    session = begin(paths)
    engine.set_control("paused", session["sessionId"], paths["control"])
    with pytest.raises(engine.InteractionError, match="paused"):
        engine.start_attempt(
            session,
            state_root=paths["state"],
            receipt_path=paths["receipts"],
            control_path=paths["control"],
        )
    paused = engine.read_session(session["sessionId"], paths["state"])
    assert paused["state"] == "paused"
    resumed = engine.resume_session(
        paused,
        state_root=paths["state"],
        receipt_path=paths["receipts"],
        control_path=paths["control"],
    )
    assert resumed["state"] == "ready"
    assert engine.control_state(paths["control"])["mode"] == "running"


def test_global_stop_blocks_new_session_before_lease(paths: dict[str, Path]) -> None:
    engine.set_control("stopped", path=paths["control"])
    with pytest.raises(engine.InteractionError, match="stopped"):
        engine.begin_session(
            owner="jaimes",
            target_host="jaimes",
            surface="browser-dom",
            intent="click",
            config_path=paths["config"],
            state_root=paths["state"],
            receipt_path=paths["receipts"],
            control_path=paths["control"],
        )
    assert not paths["state"].exists()


def test_private_context_cannot_promote_between_hosts(paths: dict[str, Path]) -> None:
    session = begin(paths)
    session["privateContext"] = True
    with pytest.raises(engine.InteractionError, match="private account context"):
        engine.promote_session(session, reason="visual-state-required", state_root=paths["state"], receipt_path=paths["receipts"])


def test_headless_promotion_uses_private_pull_queue(paths: dict[str, Path], tmp_path: Path) -> None:
    session = begin(paths)
    promotion_root = tmp_path / "promotions"
    pending = engine.promote_session(
        session,
        reason="visual-state-required",
        state_root=paths["state"],
        receipt_path=paths["receipts"],
        local_host="jaimes",
        promotion_root=promotion_root,
    )
    assert pending["state"] == "promotion-pending"
    rows = engine.export_promotion_requests(promotion_root)
    assert len(rows) == 1
    assert rows[0]["kind"] == "promote"
    completed = engine.complete_promotion_request(
        rows[0]["requestId"],
        {"status": "leased", "leaseId": "private-test-lease", "expiresAt": "2099-01-01T00:00:00Z"},
        promotion_root=promotion_root,
        state_root=paths["state"],
        receipt_path=paths["receipts"],
    )
    assert completed["state"] == "promoted"
    assert completed["host"] == "josh2"
    assert "leaseId" not in completed


def test_run_command_verifies_state_change(paths: dict[str, Path], tmp_path: Path) -> None:
    session = begin(paths)
    observed = tmp_path / "observed.txt"
    observed.write_text("before", encoding="utf-8")
    observe_spec = tmp_path / "observe.json"
    action_spec = tmp_path / "action.json"
    observe_spec.write_text(json.dumps({"command": ["/bin/cat", str(observed)]}), encoding="utf-8")
    action_spec.write_text(
        json.dumps({"command": [sys.executable, "-c", f"from pathlib import Path; Path({str(observed)!r}).write_text('after')"]}),
        encoding="utf-8",
    )
    session, verified = engine.run_reliable_command(
        session,
        action_file=action_spec,
        observe_file=observe_spec,
        config_path=paths["config"],
        state_root=paths["state"],
        receipt_path=paths["receipts"],
        control_path=paths["control"],
    )
    assert verified is True
    assert session["state"] == "complete"


def test_safe_receipt_drops_arbitrary_content_fields() -> None:
    receipt = engine.safe_receipt({"event": "verified", "url": "https://private", "pageText": "secret", "stateChanged": True})
    assert receipt["event"] == "verified"
    assert receipt["stateChanged"] is True
    assert "url" not in receipt
    assert "pageText" not in receipt


def test_attempt_budget_and_private_directories_are_hardened(paths: dict[str, Path]) -> None:
    write_config(paths["config"], attempts=5)
    session = begin(paths)
    assert session["maxAttempts"] == 3
    assert stat.S_IMODE(paths["state"].stat().st_mode) == 0o700
    assert stat.S_IMODE(paths["receipts"].parent.stat().st_mode) == 0o700


def _child_tree_command(pid_file: Path) -> list[str]:
    child = "import time; time.sleep(30)"
    parent = (
        "import pathlib,subprocess,sys,time; "
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); "
        "time.sleep(30)"
    )
    return [sys.executable, "-c", parent]


def _assert_process_not_running(pid: int) -> None:
    for _ in range(20):
        proc = __import__("subprocess").run(
            ["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, check=False
        )
        state = proc.stdout.strip()
        if proc.returncode != 0 or not state or state.startswith("Z"):
            return
        time.sleep(0.02)
    pytest.fail(f"descendant process {pid} survived interaction cancellation")


@pytest.mark.parametrize("mode", ["paused", "stopped"])
def test_operator_control_terminates_entire_process_group(paths: dict[str, Path], tmp_path: Path, mode: str) -> None:
    pid_file = tmp_path / f"{mode}.pid"
    session_id = f"ix-{mode}"
    engine.set_control("running", session_id, paths["control"])
    timer = threading.Timer(0.15, lambda: engine.set_control(mode, session_id, paths["control"]))
    timer.start()
    try:
        code, _output, reason = engine.run_private_command(
            _child_tree_command(pid_file),
            5,
            session_id,
            paths["control"],
            poll_seconds=0.02,
            terminate_grace=0.2,
        )
    finally:
        timer.cancel()
    assert code != 0
    assert reason == f"operator-{mode}"
    _assert_process_not_running(int(pid_file.read_text()))


def test_timeout_terminates_entire_process_group(paths: dict[str, Path], tmp_path: Path) -> None:
    pid_file = tmp_path / "timeout.pid"
    code, _output, reason = engine.run_private_command(
        _child_tree_command(pid_file),
        1,
        "ix-timeout",
        paths["control"],
        poll_seconds=0.02,
        terminate_grace=0.2,
    )
    assert code != 0
    assert reason == "command-timeout"
    _assert_process_not_running(int(pid_file.read_text()))
