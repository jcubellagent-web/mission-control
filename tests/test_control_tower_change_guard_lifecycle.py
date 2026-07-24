from __future__ import annotations

import importlib.util
import json
from datetime import timedelta
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "control_tower_change_guard.py"
SPEC = importlib.util.spec_from_file_location("control_tower_change_guard", MODULE_PATH)
assert SPEC and SPEC.loader
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def lease(tmp_path: Path, *, agent: str = "josh2", expired: bool = False, pid: int = 999_999) -> dict:
    backup = tmp_path / "backup"
    backup.mkdir()
    payload = {
        "agent": agent,
        "objective": "lifecycle test",
        "token": "own-token",
        "startedAt": GUARD.iso(),
        "expiresAt": GUARD.iso(GUARD.now() + timedelta(minutes=-1 if expired else 45)),
        "ownerPid": pid,
        "backup": str(backup),
        "baseCommit": "base",
        "pushApproval": None,
    }
    GUARD.LOCK_PATH.write_text(json.dumps(payload))
    return payload


@pytest.fixture(autouse=True)
def isolated_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(GUARD, "ROOT", root)
    monkeypatch.setattr(GUARD, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(GUARD, "LOCK_PATH", tmp_path / "lease.json")
    monkeypatch.setattr(GUARD, "BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(GUARD, "PUSH_POLICY_PATH", tmp_path / "push-policy.json")
    monkeypatch.setattr(GUARD, "source_changes", lambda: [])


def fake_git(*, ahead: int = 0, behind: int = 0, source_changed: bool = False):
    def run(args, **_kwargs):
        output = f"{ahead} {behind}" if "rev-list" in args else ("scripts/control_tower_change_guard.py" if "diff" in args and source_changed else "")
        return type("Process", (), {"stdout": output})()
    return run


def test_host_runtime_helpers_are_guarded_source() -> None:
    assert "scripts/mission_control_kiosk_watchdog.py" in GUARD.SOURCE_PATHS
    assert "scripts/codex_remote_manual_lane.py" in GUARD.SOURCE_PATHS
    bridge = {
        "scripts/agent_task.py",
        "scripts/agent_delegate.py",
        "scripts/linear_work_intent.py",
    }
    assert bridge.issubset(set(GUARD.SOURCE_PATHS))
    assert bridge.issubset(set(GUARD.PYTHON_COMPILE_PATHS))


def test_guard_covers_jcu10_lifecycle_brain_and_schema_paths() -> None:
    source_paths = set(GUARD.SOURCE_PATHS)
    compile_paths = set(GUARD.PYTHON_COMPILE_PATHS)
    guarded = {
        "schemas",
        "docs/brain-topic-intake.md",
        "scripts/telegram_gateway_lifecycle.py",
        "scripts/brain_media_intake.py",
        "scripts/brain_intake_worker.py",
        "scripts/brain_fixture_suite.py",
        "scripts/brain_gateway_actions.py",
        "scripts/brain_gateway_dispatcher.py",
        "scripts/brain_topic_manager.py",
        "scripts/brain_topic_catalog.py",
        "scripts/brain_topic_watcher.py",
        "scripts/josh_telegram_callback_action.py",
        "scripts/telegram_channel_registry.py",
        "scripts/telegram_lifecycle_release.py",
        "scripts/telegram_shadow_fixture.py",
        "hermes-plugins",
    }
    compiled = {path for path in guarded if path.startswith("scripts/")}
    assert guarded.issubset(source_paths)
    assert compiled.issubset(compile_paths)
    assert "hermes-plugins/jaimes-topic17-runtime-owner/__init__.py" in compile_paths
    assert "scripts/jaimes_cross_host_qc.py" not in source_paths


def test_begin_records_an_immutable_source_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    existing = GUARD.ROOT / "existing.txt"
    existing.write_text("before\n")
    monkeypatch.setattr(GUARD, "SOURCE_PATHS", ("existing.txt", "missing.txt"))
    monkeypatch.setattr(GUARD, "run", lambda *_args, **_kwargs: type("Process", (), {"stdout": "abc123\n"})())

    GUARD.begin("joshex", "snapshot test")

    payload = json.loads(GUARD.LOCK_PATH.read_text())
    assert payload["sourceSnapshot"] == [
        {"path": "existing.txt", "existedAtBegin": True},
        {"path": "missing.txt", "existedAtBegin": False},
    ]
    assert (Path(payload["backup"]) / "existing.txt").read_text() == "before\n"


def test_begin_captures_narrow_standing_push_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    GUARD.PUSH_POLICY_PATH.write_text(json.dumps({
        "schemaVersion": 1,
        "agents": {
            "joshex": {
                "enabled": True,
                "authorizedBy": "Josh",
                "authorizationRef": "user-standing-authorization-2026-07-24",
                "scope": "validated-control-tower-origin-main",
            }
        },
    }))
    monkeypatch.setattr(GUARD, "SOURCE_PATHS", ())
    monkeypatch.setattr(GUARD, "run", lambda *_args, **_kwargs: type("Process", (), {"stdout": "abc123\n"})())

    GUARD.begin("joshex", "standing push test")

    payload = json.loads(GUARD.LOCK_PATH.read_text())
    assert payload["pushApproval"]["standing"] is True
    assert payload["pushApproval"]["scope"] == "validated-control-tower-origin-main"


def test_standing_push_approval_rejects_broader_or_unverified_policy() -> None:
    GUARD.PUSH_POLICY_PATH.write_text(json.dumps({
        "agents": {
            "joshex": {
                "enabled": True,
                "authorizedBy": "Josh",
                "authorizationRef": "too-broad",
                "scope": "all-production-pushes",
            }
        },
    }))

    assert GUARD.standing_push_approval("joshex") is None


def test_extend_snapshot_adds_only_newly_guarded_absent_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = lease(tmp_path)
    payload["sourceSnapshot"] = [
        {"path": "scripts/existing.py", "existedAtBegin": True},
    ]
    GUARD.LOCK_PATH.write_text(json.dumps(payload))
    monkeypatch.setattr(
        GUARD,
        "SOURCE_PATHS",
        ("scripts/existing.py", "scripts/new_worker.py", "scripts/new_dispatcher.py"),
    )

    GUARD.extend_snapshot("own-token")

    extended = json.loads(GUARD.LOCK_PATH.read_text())
    assert extended["sourceSnapshot"] == [
        {"path": "scripts/existing.py", "existedAtBegin": True},
        {"path": "scripts/new_worker.py", "existedAtBegin": False},
        {"path": "scripts/new_dispatcher.py", "existedAtBegin": False},
    ]


def test_extend_snapshot_rejects_a_newly_guarded_path_that_already_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = lease(tmp_path)
    payload["sourceSnapshot"] = []
    GUARD.LOCK_PATH.write_text(json.dumps(payload))
    existing = GUARD.ROOT / "scripts" / "untracked.py"
    existing.parent.mkdir(parents=True)
    existing.write_text("cannot prove begin state\n")
    monkeypatch.setattr(GUARD, "SOURCE_PATHS", ("scripts/untracked.py",))

    with pytest.raises(SystemExit, match="begin-state cannot be proven"):
        GUARD.extend_snapshot("own-token")

    assert json.loads(GUARD.LOCK_PATH.read_text())["sourceSnapshot"] == []


def test_successful_completion_verifies_and_releases_own_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = lease(tmp_path)
    payload["pushApproval"] = {"reference": "approved test"}
    GUARD.LOCK_PATH.write_text(json.dumps(payload))
    verified = []
    monkeypatch.setattr(GUARD, "verify", lambda token: verified.append(token))
    monkeypatch.setattr(GUARD, "run", fake_git(source_changed=True))

    GUARD.finish("own-token")

    assert verified == ["own-token"]
    assert not GUARD.LOCK_PATH.exists()


def test_verification_failure_keeps_lease_for_repair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lease(tmp_path)
    monkeypatch.setattr(GUARD, "verify", lambda _token: (_ for _ in ()).throw(SystemExit("failed")))

    with pytest.raises(SystemExit, match="failed"):
        GUARD.finish("own-token")

    assert GUARD.LOCK_PATH.exists()


def test_cancellation_aborts_own_lease_and_preserves_evidence(tmp_path: Path) -> None:
    payload = lease(tmp_path)
    backup = Path(payload["backup"])

    GUARD.abort("own-token")

    assert not GUARD.LOCK_PATH.exists()
    assert json.loads((backup / "lifecycle-outcome.json").read_text())["outcome"] == "aborted"


def test_cancellation_restores_existing_source_and_removes_new_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = lease(tmp_path)
    backup = Path(payload["backup"])
    root = tmp_path / "repo"
    existing = root / "scripts" / "existing.py"
    created = root / "scripts" / "created.py"
    backup_existing = backup / "scripts" / "existing.py"
    existing.parent.mkdir(parents=True)
    backup_existing.parent.mkdir(parents=True)
    existing.write_text("edited\n")
    created.write_text("new during lease\n")
    backup_existing.write_text("original\n")
    monkeypatch.setattr(GUARD, "ROOT", root)
    monkeypatch.setattr(GUARD, "SOURCE_PATHS", ("scripts/existing.py", "scripts/created.py"))
    payload["sourceSnapshot"] = [
        {"path": "scripts/existing.py", "existedAtBegin": True},
        {"path": "scripts/created.py", "existedAtBegin": False},
    ]
    GUARD.LOCK_PATH.write_text(json.dumps(payload))

    GUARD.abort("own-token")

    assert existing.read_text() == "original\n"
    assert not created.exists()
    assert not GUARD.LOCK_PATH.exists()


def test_abort_uses_lease_snapshot_when_guard_paths_expand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = lease(tmp_path)
    root = GUARD.ROOT
    existing = root / "existing.py"
    later_guarded = root / "later.py"
    existing.write_text("edited\n")
    later_guarded.write_text("must remain\n")
    (Path(payload["backup"]) / "existing.py").write_text("original\n")
    payload["sourceSnapshot"] = [{"path": "existing.py", "existedAtBegin": True}]
    GUARD.LOCK_PATH.write_text(json.dumps(payload))
    monkeypatch.setattr(GUARD, "SOURCE_PATHS", ("existing.py", "later.py"))

    GUARD.abort("own-token")

    assert existing.read_text() == "original\n"
    assert later_guarded.read_text() == "must remain\n"


def test_legacy_abort_preserves_unbacked_existing_path_and_retains_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease(tmp_path)
    unbacked = GUARD.ROOT / "newly-guarded.py"
    unbacked.write_text("pre-existing but not backed up\n")
    monkeypatch.setattr(GUARD, "SOURCE_PATHS", ("newly-guarded.py",))

    with pytest.raises(SystemExit, match="rollback prevalidation failed"):
        GUARD.abort("own-token")

    assert unbacked.read_text() == "pre-existing but not backed up\n"
    assert GUARD.LOCK_PATH.exists()


def test_snapshot_abort_fails_closed_before_any_partial_restore(
    tmp_path: Path,
) -> None:
    payload = lease(tmp_path)
    first = GUARD.ROOT / "first.py"
    second = GUARD.ROOT / "second.py"
    first.write_text("edited first\n")
    second.write_text("edited second\n")
    (Path(payload["backup"]) / "first.py").write_text("original first\n")
    payload["sourceSnapshot"] = [
        {"path": "first.py", "existedAtBegin": True},
        {"path": "second.py", "existedAtBegin": True},
    ]
    GUARD.LOCK_PATH.write_text(json.dumps(payload))

    with pytest.raises(SystemExit, match="rollback prevalidation failed"):
        GUARD.abort("own-token")

    assert first.read_text() == "edited first\n"
    assert second.read_text() == "edited second\n"
    assert GUARD.LOCK_PATH.exists()


def test_process_interruption_uses_finally_style_abort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = lease(tmp_path)
    monkeypatch.setattr(GUARD, "begin", lambda *_args: None)

    with pytest.raises(KeyboardInterrupt):
        with GUARD.leased_edit("josh2", "interruptible"):
            raise KeyboardInterrupt()

    assert not GUARD.LOCK_PATH.exists()
    assert json.loads((Path(payload["backup"]) / "lifecycle-outcome.json").read_text())["outcome"] == "aborted"


def test_expired_orphan_recovers_only_when_owner_absent_and_source_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = lease(tmp_path, expired=True)
    monkeypatch.setattr(GUARD, "process_is_alive", lambda _pid: False)

    GUARD.recover_expired()

    assert not GUARD.LOCK_PATH.exists()
    assert json.loads((Path(payload["backup"]) / "lifecycle-outcome.json").read_text())["outcome"] == "expired-orphan-recovered"


def test_expired_orphan_refuses_dirty_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lease(tmp_path, expired=True)
    monkeypatch.setattr(GUARD, "source_changes", lambda: ["scripts/control_tower_change_guard.py"])
    monkeypatch.setattr(GUARD, "process_is_alive", lambda _pid: False)

    with pytest.raises(SystemExit):
        GUARD.recover_expired()

    assert GUARD.LOCK_PATH.exists()


def test_missing_push_closure_keeps_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lease(tmp_path)
    monkeypatch.setattr(GUARD, "verify", lambda _token: None)
    monkeypatch.setattr(GUARD, "run", fake_git(ahead=1))

    with pytest.raises(SystemExit, match="commit/push closure incomplete"):
        GUARD.finish("own-token")

    assert GUARD.LOCK_PATH.exists()


def test_missing_push_approval_keeps_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lease(tmp_path)
    monkeypatch.setattr(GUARD, "verify", lambda _token: None)
    monkeypatch.setattr(GUARD, "run", fake_git(source_changed=True))

    with pytest.raises(SystemExit, match="explicit push approval"):
        GUARD.finish("own-token")

    assert GUARD.LOCK_PATH.exists()


def test_active_lease_owned_by_another_agent_cannot_be_released(tmp_path: Path) -> None:
    lease(tmp_path, agent="jaimes")

    with pytest.raises(SystemExit, match="leased by jaimes"):
        GUARD.finish("not-the-owner")

    assert GUARD.LOCK_PATH.exists()
