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
    monkeypatch.setattr(GUARD, "LOCK_PATH", tmp_path / "lease.json")
    monkeypatch.setattr(GUARD, "source_changes", lambda: [])


def fake_git(*, ahead: int = 0, behind: int = 0, source_changed: bool = False):
    def run(args, **_kwargs):
        output = f"{ahead} {behind}" if "rev-list" in args else ("scripts/control_tower_change_guard.py" if "diff" in args and source_changed else "")
        return type("Process", (), {"stdout": output})()
    return run


def test_host_runtime_helpers_are_guarded_source() -> None:
    assert "scripts/mission_control_kiosk_watchdog.py" in GUARD.SOURCE_PATHS
    assert "scripts/codex_remote_manual_lane.py" in GUARD.SOURCE_PATHS


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

    GUARD.abort("own-token")

    assert existing.read_text() == "original\n"
    assert not created.exists()
    assert not GUARD.LOCK_PATH.exists()


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
