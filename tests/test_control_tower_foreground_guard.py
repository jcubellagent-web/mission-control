from __future__ import annotations

import importlib.util
import json
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "control_tower_foreground.py"
SPEC = importlib.util.spec_from_file_location("control_tower_foreground", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


NOW = datetime(2026, 7, 16, 3, 30, tzinfo=timezone.utc)


def ensure(tmp_path: Path, **overrides):
    options = {
        "lease_path": tmp_path / "foreground-work.json",
        "now": NOW,
        "locked_fn": lambda: False,
        "idle_fn": lambda: 600.0,
        "kiosk_pid_fn": lambda: 101,
        "cdp_ready_fn": lambda: True,
        "frontmost_fn": lambda: {"pid": 202, "name": "Google Chrome"},
        "activate_fn": lambda kiosk_pid, previous_pid: (True, f"focused {kiosk_pid} from {previous_pid}"),
    }
    options.update(overrides)
    return MODULE.ensure_foreground(**options)


def test_wrong_chrome_profile_restores_exact_kiosk_pid(tmp_path: Path) -> None:
    calls = []

    result = ensure(
        tmp_path,
        activate_fn=lambda kiosk_pid, previous_pid: (calls.append((kiosk_pid, previous_pid)) or True, "focused"),
    )

    assert result["ok"] is True
    assert result["status"] == "focused"
    assert result["kioskPid"] == 101
    assert result["previousFrontmostPid"] == 202
    assert calls == [(101, 202)]


def test_already_foreground_is_a_noop_even_after_recent_input(tmp_path: Path) -> None:
    result = ensure(
        tmp_path,
        idle_fn=lambda: 1.0,
        frontmost_fn=lambda: {"pid": 101, "name": "Google Chrome"},
        activate_fn=lambda *_: pytest.fail("activation should not run"),
    )

    assert result["status"] == "foreground"
    assert result["reason"] == "already-foreground"


def test_recent_physical_input_defers_takeover(tmp_path: Path) -> None:
    result = ensure(
        tmp_path,
        idle_fn=lambda: MODULE.RECENT_INPUT_SECONDS - 0.1,
        activate_fn=lambda *_: pytest.fail("activation should not run"),
    )

    assert result["ok"] is True
    assert result["status"] == "deferred"
    assert result["reason"] == "recent-physical-input"


def test_input_threshold_equality_restores_kiosk(tmp_path: Path) -> None:
    result = ensure(tmp_path, idle_fn=lambda: MODULE.RECENT_INPUT_SECONDS)

    assert result["status"] == "focused"


def test_locked_session_defers_without_probing_or_activating(tmp_path: Path) -> None:
    result = ensure(
        tmp_path,
        locked_fn=lambda: True,
        kiosk_pid_fn=lambda: pytest.fail("kiosk lookup should not run"),
        activate_fn=lambda *_: pytest.fail("activation should not run"),
    )

    assert result == {"ok": True, "status": "deferred", "reason": "session-locked"}


def test_protected_system_app_is_never_hidden(tmp_path: Path) -> None:
    result = ensure(
        tmp_path,
        frontmost_fn=lambda: {"pid": 303, "name": "SecurityAgent"},
        activate_fn=lambda *_: pytest.fail("activation should not run"),
    )

    assert result["status"] == "deferred"
    assert result["reason"] == "protected-system-session"


def test_fresh_visible_work_lease_defers(tmp_path: Path) -> None:
    lease_path = tmp_path / "foreground-work.json"
    payload = MODULE.begin_lease(
        owner="joshex",
        purpose="computer-use",
        ttl_seconds=180,
        path=lease_path,
        now=NOW,
    )

    result = ensure(
        tmp_path,
        lease_path=lease_path,
        activate_fn=lambda *_: pytest.fail("activation should not run"),
    )

    assert payload["leaseId"]
    assert result["status"] == "deferred"
    assert result["reason"] == "active-visible-work"
    assert result["work"] == {
        "owner": "joshex",
        "purpose": "computer-use",
        "expiresAt": "2026-07-16T03:33:00Z",
    }
    assert "leaseId" not in result["work"]
    assert stat.S_IMODE(lease_path.stat().st_mode) == 0o600


def test_expired_lease_is_cleaned_and_does_not_suppress(tmp_path: Path) -> None:
    lease_path = tmp_path / "foreground-work.json"
    MODULE.begin_lease(
        owner="josh2",
        purpose="browser",
        ttl_seconds=30,
        path=lease_path,
        now=NOW - timedelta(seconds=30),
    )

    result = ensure(tmp_path, lease_path=lease_path)

    assert result["status"] == "focused"
    assert not lease_path.exists()


def test_dead_pid_lease_does_not_suppress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lease_path = tmp_path / "foreground-work.json"
    monkeypatch.setattr(MODULE, "process_start_fingerprint", lambda _pid: "start-a")
    MODULE.begin_lease(
        owner="josh2",
        purpose="local-ui",
        ttl_seconds=180,
        pid=444,
        path=lease_path,
        now=NOW,
    )
    monkeypatch.setattr(MODULE, "process_start_fingerprint", lambda _pid: None)

    result = ensure(tmp_path, lease_path=lease_path)

    assert result["status"] == "focused"
    assert not lease_path.exists()


def test_lease_renew_and_release_require_matching_id(tmp_path: Path) -> None:
    lease_path = tmp_path / "foreground-work.json"
    payload = MODULE.begin_lease(
        owner="josh2",
        purpose="browser",
        path=lease_path,
        now=NOW,
    )

    with pytest.raises(PermissionError):
        MODULE.renew_lease(lease_id="wrong", path=lease_path, now=NOW)
    renewed = MODULE.renew_lease(
        lease_id=payload["leaseId"],
        ttl_seconds=300,
        path=lease_path,
        now=NOW + timedelta(seconds=10),
    )
    assert renewed["expiresAt"] == "2026-07-16T03:35:10Z"
    with pytest.raises(PermissionError):
        MODULE.end_lease(lease_id="wrong", path=lease_path)
    assert MODULE.end_lease(lease_id=payload["leaseId"], path=lease_path)["ended"] is True
    assert not lease_path.exists()


def test_malformed_or_excessive_lease_does_not_suppress(tmp_path: Path) -> None:
    lease_path = tmp_path / "foreground-work.json"
    lease_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "leaseId": "opaque",
                "owner": "josh2",
                "purpose": "browser",
                "startedAt": "2026-07-16T03:30:00Z",
                "heartbeatAt": "2026-07-16T03:30:00Z",
                "expiresAt": "2026-07-16T04:30:00Z",
            }
        )
    )

    result = ensure(tmp_path, lease_path=lease_path)

    assert result["status"] == "focused"
    assert not lease_path.exists()


def test_activation_failure_is_reported_without_false_success(tmp_path: Path) -> None:
    result = ensure(
        tmp_path,
        activate_fn=lambda *_: (False, "foreground verification failed"),
    )

    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["reason"] == "activation-failed"


def test_exact_cdp_target_activation_verifies_kiosk_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"Target activated"

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr(MODULE, "control_tower_target_id", lambda: "target/id")
    monkeypatch.setattr(MODULE.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(MODULE, "frontmost_application", lambda: {"pid": 101, "name": "Google Chrome"})
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)

    ok, detail = MODULE.activate_kiosk_process(101, 202)

    assert ok is True
    assert "physical foreground" in detail
    assert len(requests) == 1
    request, timeout = requests[0]
    assert request.get_method() == "PUT"
    assert request.full_url.endswith("/json/activate/target%2Fid")
    assert timeout == 3


def test_cdp_activation_command_success_without_pid_change_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"Target activated"

    monkeypatch.setattr(MODULE, "control_tower_target_id", lambda: "target")
    monkeypatch.setattr(MODULE.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(MODULE, "frontmost_application", lambda: {"pid": 202, "name": "Google Chrome"})
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)
    hidden_calls = []
    monkeypatch.setattr(
        MODULE,
        "set_application_hidden",
        lambda pid, hidden: (hidden_calls.append((pid, hidden)) or True),
    )

    ok, detail = MODULE.activate_kiosk_process(101, 202)

    assert ok is False
    assert "failed verification" in detail
    assert hidden_calls == [(202, True), (202, False)]


def test_same_bundle_retry_hides_only_previous_process_then_focuses_kiosk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"Target activated"

    frontmost = iter(
        [
            {"pid": 202, "name": "Google Chrome"},
            {"pid": 101, "name": "Google Chrome"},
        ]
    )
    hidden_calls = []
    monkeypatch.setattr(MODULE, "control_tower_target_id", lambda: "target")
    monkeypatch.setattr(MODULE.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(MODULE, "frontmost_application", lambda: next(frontmost))
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        MODULE,
        "set_application_hidden",
        lambda pid, hidden: (hidden_calls.append((pid, hidden)) or True),
    )

    ok, _detail = MODULE.activate_kiosk_process(101, 202)

    assert ok is True
    assert hidden_calls == [(202, True)]


def test_missing_kiosk_is_an_error_without_repair(tmp_path: Path) -> None:
    result = ensure(tmp_path, kiosk_pid_fn=lambda: None, cdp_ready_fn=lambda: False)

    assert result["ok"] is False
    assert result["status"] == "missing"
    assert result["reason"] == "kiosk-process-missing"


def test_process_match_rejects_chrome_renderer_and_similar_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = f"--user-data-dir={MODULE.KIOSK_PROFILE}"
    output = "\n".join(
        [
            f"  10 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome {expected}-old --remote-debugging-port=9224",
            f"  11 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome {expected} --type=renderer",
            f"  12 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome {expected} --remote-debugging-port=9224",
        ]
    )

    class Result:
        returncode = 0
        stdout = output
        stderr = ""

    monkeypatch.setattr(MODULE, "run", lambda *_args, **_kwargs: Result())

    assert MODULE.find_kiosk_pid() == 12
