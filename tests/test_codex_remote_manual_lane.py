from __future__ import annotations

import importlib.util
import json
import plistlib
import sqlite3
import stat
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "codex_remote_manual_lane.py"
SPEC = importlib.util.spec_from_file_location("codex_remote_manual_lane", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def profile(tmp_path: Path, agent: str = "josh2"):
    return MODULE.profile_for(agent, home=tmp_path)


def test_human_title_adds_host_prefix_and_rejects_opaque_ids(tmp_path: Path) -> None:
    item = profile(tmp_path)

    assert MODULE.human_title(item, "Review Telegram gateway health") == (
        "Josh 2.0 — Review Telegram gateway health"
    )
    assert MODULE.human_title(item, "Josh 2.0 — Review Telegram gateway health") == (
        "Josh 2.0 — Review Telegram gateway health"
    )
    with pytest.raises(ValueError, match="plain-English purpose"):
        MODULE.human_title(item, "019f7865-cd49-7131-b45b-ae986f0fa57d")
    with pytest.raises(ValueError, match="plain-English purpose"):
        MODULE.human_title(item, "task-991bb9af441d")
    with pytest.raises(ValueError, match="plain-English purpose"):
        MODULE.human_title(item, "New task")


def test_title_repairs_are_scoped_to_interactive_manual_threads(tmp_path: Path) -> None:
    item = profile(tmp_path, "jaimes")
    workspace = str(item.workspace)
    threads = [
        {
            "id": "manual-id",
            "name": "aabbccddeeff00112233",
            "preview": "Review the overnight Sorare automation health and summarize failures.",
            "cwd": workspace,
            "source": "vscode",
            "ephemeral": False,
            "createdAt": 1784437200,
        },
        {
            "id": "prefix-id",
            "name": "Check long-running job capacity",
            "preview": "",
            "cwd": workspace,
            "source": "cli",
            "ephemeral": False,
        },
        {
            "id": "already-clear",
            "name": "JAIMES — Inspect overnight research queue",
            "preview": "",
            "cwd": workspace,
            "source": "appServer",
            "ephemeral": False,
        },
        {
            "id": "background-id",
            "name": "deadbeefdeadbeefdeadbeef",
            "preview": "Background task",
            "cwd": workspace,
            "source": "exec",
            "ephemeral": False,
        },
        {
            "id": "other-workspace-id",
            "name": "deadbeefdeadbeefdeadbeef",
            "preview": "Other workspace task",
            "cwd": str(tmp_path / "other"),
            "source": "vscode",
            "ephemeral": False,
        },
    ]

    repairs = MODULE.title_repairs(threads, item)

    assert repairs == [
        (
            "manual-id",
            "JAIMES — Untitled manual task — July 19, 2026 at 1:00 AM EDT (reference manualid)",
        ),
        ("prefix-id", "JAIMES — Check long-running job capacity"),
    ]


def test_sensitive_or_blank_preview_gets_readable_date_not_raw_content(tmp_path: Path) -> None:
    item = profile(tmp_path)
    thread = {
        "id": "opaque-internal-id",
        "name": "",
        "preview": "Use password hunter2 to inspect https://private.example.invalid",
        "cwd": str(item.workspace),
        "source": "vscode",
        "ephemeral": False,
        "createdAt": 1784437200,
    }

    title = MODULE.repaired_title(item, thread)

    assert title.startswith("Josh 2.0 — Untitled manual task — ")
    assert "hunter2" not in title
    assert "private.example" not in title
    assert "opaque-internal-id" not in title


class FakeClient:
    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.calls: list[tuple[str, dict]] = []
        self.name = None

    def request(self, method: str, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == "thread/start":
            return {
                "thread": {
                    "id": "019f-readable-secondary-id",
                    "name": None,
                    "cwd": self.workspace,
                    "source": "vscode",
                    "ephemeral": False,
                }
            }
        if method == "thread/name/set":
            self.name = params["name"]
            return {}
        if method == "thread/read":
            return {
                "thread": {
                    "id": "019f-readable-secondary-id",
                    "name": self.name,
                    "cwd": self.workspace,
                    "source": "vscode",
                    "ephemeral": False,
                    "turns": [],
                }
            }
        if method == "turn/start":
            return {"turn": {"id": "turn-secondary-id"}}
        if method == "model/list":
            return {
                "data": [
                    {
                        "model": "gpt-5.6-luna",
                        "supportedReasoningEfforts": [{"reasoningEffort": "low"}],
                    }
                ]
            }
        if method == "thread/list":
            if self.name is None:
                return {"data": [], "nextCursor": None}
            return {
                "data": [
                    {
                        "id": "019f-readable-secondary-id",
                        "name": self.name,
                        "cwd": self.workspace,
                        "source": "vscode",
                        "ephemeral": False,
                    }
                ],
                "nextCursor": None,
            }
        raise AssertionError(method)

    def wait_for_turn_completion(self, thread_id: str, turn_id: str, *, timeout: int = 45):
        self.calls.append(("wait_for_turn_completion", {"threadId": thread_id, "turnId": turn_id}))
        return {"id": turn_id, "status": "completed", "error": None}


def test_create_labeled_thread_uses_one_bounded_readiness_turn(tmp_path: Path) -> None:
    item = profile(tmp_path)
    client = FakeClient(str(item.workspace))

    result = MODULE.create_labeled_thread(
        client,
        item,
        "Remote workspace ready",
        "Confirm this manual Remote workspace is ready. Make no changes.",
    )

    assert result["title"] == "Josh 2.0 — Remote workspace ready"
    assert [method for method, _params in client.calls] == [
        "thread/list",
        "thread/start",
        "thread/name/set",
        "thread/read",
        "model/list",
        "turn/start",
        "wait_for_turn_completion",
        "thread/list",
    ]
    turn = next(params for method, params in client.calls if method == "turn/start")
    assert turn["model"] == "gpt-5.6-luna"
    assert turn["effort"] == "low"
    assert turn["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert turn["approvalPolicy"] == "never"
    assert turn["summary"] == "none"
    assert turn["input"][0]["text_elements"] == []
    assert turn["clientUserMessageId"].startswith("remote-readiness-check-")
    assert result["modelTurnStarted"] is True
    assert result["threadId"] == "019f-readable-secondary-id"


def test_create_labeled_thread_is_idempotent(tmp_path: Path) -> None:
    item = profile(tmp_path)
    client = FakeClient(str(item.workspace))
    client.name = "Josh 2.0 — Remote workspace ready"

    result = MODULE.create_labeled_thread(
        client,
        item,
        "Remote workspace ready",
        "Confirm readiness.",
    )

    assert result["created"] is False
    assert [method for method, _params in client.calls] == ["thread/list"]
    assert result["title"] == "Josh 2.0 — Remote workspace ready"


def test_invisible_persisted_readiness_thread_is_reused(tmp_path: Path) -> None:
    item = profile(tmp_path)
    item.state_db_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(item.state_db_path)
    connection.execute(
        """
        CREATE TABLE threads (
            id TEXT,
            title TEXT,
            source TEXT,
            has_user_event INTEGER,
            first_user_message TEXT,
            tokens_used INTEGER,
            created_at INTEGER,
            updated_at INTEGER,
            cwd TEXT,
            archived INTEGER
        )
        """
    )
    connection.execute(
        "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "persisted-readable-secondary-id",
            "Josh 2.0 — Remote workspace ready",
            "vscode",
            0,
            "",
            0,
            1784437200,
            1784437200,
            str(item.workspace),
            0,
        ),
    )
    connection.commit()
    connection.close()

    class PersistedClient:
        def __init__(self):
            self.visible = False
            self.calls = []

        def request(self, method: str, params=None):
            params = params or {}
            self.calls.append((method, params))
            if method == "thread/list":
                data = []
                if self.visible:
                    data = [
                        {
                            "id": "persisted-readable-secondary-id",
                            "name": "Josh 2.0 — Remote workspace ready",
                            "cwd": str(item.workspace),
                            "source": "vscode",
                            "ephemeral": False,
                        }
                    ]
                return {"data": data, "nextCursor": None}
            if method == "thread/read":
                return {
                    "thread": {
                        "id": "persisted-readable-secondary-id",
                        "name": "Josh 2.0 — Remote workspace ready",
                        "cwd": str(item.workspace),
                        "source": "vscode",
                        "ephemeral": False,
                    }
                }
            if method == "thread/resume":
                return {"thread": {"id": "persisted-readable-secondary-id"}}
            if method == "model/list":
                return {"data": [{"model": "gpt-5.6-luna", "supportedReasoningEfforts": []}]}
            if method == "turn/start":
                self.visible = True
                return {"turn": {"id": "readiness-turn"}}
            raise AssertionError(method)

        def wait_for_turn_completion(self, thread_id: str, turn_id: str, *, timeout: int = 45):
            self.calls.append(("wait_for_turn_completion", {"threadId": thread_id, "turnId": turn_id}))
            return {"id": turn_id, "status": "completed", "error": None}

    client = PersistedClient()
    result = MODULE.create_labeled_thread(
        client,
        item,
        "Remote workspace ready",
        "Confirm readiness without making changes.",
    )

    methods = [method for method, _params in client.calls]
    assert result["created"] is False
    assert result["modelTurnStarted"] is True
    assert "thread/start" not in methods
    assert "thread/name/set" not in methods
    assert methods.count("thread/resume") == 1
    assert methods.count("turn/start") == 1


def test_configure_preserves_state_and_sets_clear_workspace_labels(tmp_path: Path) -> None:
    item = profile(tmp_path, "jaimes")
    item.workspace.mkdir(parents=True)
    (item.workspace / "AGENTS.md").write_text("# Existing rules\n", encoding="utf-8")
    (item.workspace / "README.md").write_text("# Existing guide\n", encoding="utf-8")
    item.remote_control_plist_path.parent.mkdir(parents=True)
    item.remote_control_plist_path.write_bytes(
        plistlib.dumps(
            {
                "Label": MODULE.REMOTE_CONTROL_LABEL,
                "ProgramArguments": ["codex", "app-server"],
                "WorkingDirectory": str(tmp_path),
                "KeepAlive": True,
            }
        )
    )
    item.remote_control_plist_path.chmod(0o600)
    item.global_state_path.parent.mkdir(parents=True)
    item.global_state_path.write_text(
        json.dumps(
            {
                "unrelated-setting": {"preserved": True},
                "active-workspace-roots": [str(tmp_path / "old")],
                "electron-saved-workspace-roots": [str(tmp_path / "old")],
                "electron-workspace-root-labels": {str(tmp_path / "old"): "Old project"},
                "electron-workspace-project-order": [str(tmp_path / "old")],
            }
        ),
        encoding="utf-8",
    )
    item.global_state_path.chmod(0o644)
    helper_path = tmp_path / "mission-control" / "scripts" / "codex_remote_manual_lane.py"
    helper_path.parent.mkdir(parents=True)
    helper_path.write_text("def main():\n    return 0\n", encoding="utf-8")

    first = MODULE.configure_host_files(item, helper_path=helper_path)
    second = MODULE.configure_host_files(item, helper_path=helper_path)

    assert first["workspace"] == "JAIMES — Manual Remote work"
    assert second == first
    state = json.loads(item.global_state_path.read_text(encoding="utf-8"))
    assert state["unrelated-setting"] == {"preserved": True}
    assert state["active-workspace-roots"] == [str(item.workspace)]
    assert state["electron-saved-workspace-roots"] == [str(item.workspace)]
    assert state["electron-workspace-root-labels"][str(item.workspace)] == (
        "JAIMES — Manual Remote work"
    )
    assert state["electron-workspace-project-order"][0] == str(item.workspace)

    remote_plist = plistlib.loads(item.remote_control_plist_path.read_bytes())
    assert remote_plist["WorkingDirectory"] == str(item.workspace)
    assert remote_plist["KeepAlive"] is True
    assert remote_plist["ProgramArguments"] == ["codex", "app-server"]
    assert stat.S_IMODE(item.remote_control_plist_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(item.global_state_path.stat().st_mode) == 0o644

    guard_plist = plistlib.loads(item.title_guard_plist_path.read_bytes())
    assert guard_plist["WorkingDirectory"] == str(item.workspace)
    assert guard_plist["ProgramArguments"][:2] == [
        "/opt/homebrew/bin/python3",
        str(item.runtime_helper_path),
    ]
    assert guard_plist["ProgramArguments"][2:] == ["guard", "--agent", "jaimes", "--quiet"]
    assert item.runtime_helper_path.read_bytes() == helper_path.read_bytes()
    assert stat.S_IMODE(item.runtime_helper_path.stat().st_mode) == 0o755

    agents = (item.workspace / "AGENTS.md").read_text(encoding="utf-8")
    readme = (item.workspace / "README.md").read_text(encoding="utf-8")
    assert agents.startswith("# Existing rules")
    assert readme.startswith("# Existing guide")
    assert agents.count(f"<!-- {MODULE.MANAGED_MARKER}:start -->") == 1
    assert readme.count(f"<!-- {MODULE.MANAGED_MARKER}:start -->") == 1
    assert "JAIMES — <plain-English purpose>" in agents
    assert "background agent jobs intentionally do not appear here" in readme


def test_configure_rolls_back_every_target_on_late_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = profile(tmp_path)
    item.workspace.mkdir(parents=True)
    agents_path = item.workspace / "AGENTS.md"
    readme_path = item.workspace / "README.md"
    agents_path.write_text("original agents\n", encoding="utf-8")
    readme_path.write_text("original readme\n", encoding="utf-8")
    item.remote_control_plist_path.parent.mkdir(parents=True)
    item.remote_control_plist_path.write_bytes(
        plistlib.dumps({"Label": MODULE.REMOTE_CONTROL_LABEL, "WorkingDirectory": str(tmp_path)})
    )
    item.global_state_path.parent.mkdir(parents=True)
    item.global_state_path.write_text('{"preserve": true}\n', encoding="utf-8")
    helper_path = tmp_path / "source" / "codex_remote_manual_lane.py"
    helper_path.parent.mkdir()
    helper_path.write_text("def main():\n    return 0\n", encoding="utf-8")
    originals = {
        path: path.read_bytes()
        for path in (
            agents_path,
            readme_path,
            item.remote_control_plist_path,
            item.global_state_path,
        )
    }
    real_atomic_write_plist = MODULE.atomic_write_plist

    def fail_guard(path, payload, **kwargs):
        if path == item.title_guard_plist_path:
            raise OSError("simulated late write failure")
        return real_atomic_write_plist(path, payload, **kwargs)

    monkeypatch.setattr(MODULE, "atomic_write_plist", fail_guard)

    with pytest.raises(OSError, match="simulated late write failure"):
        MODULE.configure_host_files(item, helper_path=helper_path)

    for path, payload in originals.items():
        assert path.read_bytes() == payload, path
    assert not item.runtime_helper_path.exists()
    assert not item.title_guard_plist_path.exists()


def test_quiet_guard_logs_failures_to_stderr(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    exit_code = MODULE.main(["guard", "--agent", "josh2", "--home", str(tmp_path), "--quiet"])

    captured = capsys.readouterr()
    assert exit_code == 69
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["ok"] is False
    assert error["agent"] == "Josh 2.0"
    assert error["operation"] == "guard"


def test_transport_parse_error_is_sanitized() -> None:
    class BrokenConnection:
        def send(self, _payload):
            return None

        def recv(self):
            return "not-json"

    client = MODULE.AppServerClient(Path("/unused"))
    client._connection = BrokenConnection()

    with pytest.raises(MODULE.RemoteProtocolError, match="invalid transport response") as exc:
        client.request("thread/list", {})

    assert "not-json" not in str(exc.value)


def test_server_error_message_is_sanitized() -> None:
    class ErrorConnection:
        def send(self, _payload):
            return None

        def recv(self):
            return json.dumps(
                {
                    "id": 1,
                    "error": {
                        "code": -32602,
                        "message": "Invalid prompt: private customer identifier 12345",
                    },
                }
            )

    client = MODULE.AppServerClient(Path("/unused"))
    client._connection = ErrorConnection()

    with pytest.raises(MODULE.RemoteProtocolError, match=r"thread/start: request failed \(-32602\)") as exc:
        client.request("thread/start", {"prompt": "private customer identifier 12345"})

    assert "private customer identifier" not in str(exc.value)


def test_guard_summary_never_echoes_thread_titles_or_ids(tmp_path: Path) -> None:
    item = profile(tmp_path)

    class GuardClient:
        def request(self, method: str, params=None):
            if method == "thread/list":
                return {
                    "data": [
                        {
                            "id": "private-thread-id",
                            "name": "deadbeefdeadbeefdeadbeef",
                            "preview": "Assess current Telegram gateway health",
                            "cwd": str(item.workspace),
                            "source": "vscode",
                            "ephemeral": False,
                        }
                    ],
                    "nextCursor": None,
                }
            if method == "thread/name/set":
                return {}
            raise AssertionError(method)

    result = MODULE.guard_manual_titles(GuardClient(), item)
    rendered = json.dumps(result)

    assert result["renamed"] == 1
    assert "private-thread-id" not in rendered
    assert "Telegram gateway health" not in rendered
