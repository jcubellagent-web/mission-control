from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCOPED = load("scoped_change_guard", ROOT / "scripts" / "scoped_change_guard.py")
DEPLOY = load("immutable_deploy_bundle", ROOT / "scripts" / "immutable_deploy_bundle.py")
PREFLIGHT = load("ecosystem_edit_preflight", ROOT / "scripts" / "ecosystem_edit_preflight.py")


def command(repo: Path, *args: str) -> str:
    return subprocess.check_output(args, cwd=repo, text=True).strip()


def make_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    command(repo, "git", "init", "-b", "main")
    command(repo, "git", "config", "user.email", "test@example.com")
    command(repo, "git", "config", "user.name", "Test")
    for relative, value in (("src/a.py", "a = 1\n"), ("docs/b.md", "b\n"), ("shared.txt", "shared\n")):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    command(repo, "git", "add", ".")
    command(repo, "git", "commit", "-m", "base")
    first, second = tmp_path / "work-a", tmp_path / "work-b"
    command(repo, "git", "worktree", "add", "-b", "task/a", str(first), "HEAD")
    command(repo, "git", "worktree", "add", "-b", "task/b", str(second), "HEAD")
    return repo, first, second


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    tasks = tmp_path / "tasks.json"
    task_rows = [
        {"id": "task-a", "workId": "work-a", "runId": "run-a", "status": "active", "workScope": "shared-source"},
        {"id": "task-b", "workId": "work-b", "runId": "run-b", "status": "active", "workScope": "shared-source"},
    ]
    tasks.write_text(json.dumps({"tasks": task_rows}))
    monkeypatch.setattr(SCOPED, "STATE_DIR", state)
    monkeypatch.setattr(SCOPED, "REGISTRY_PATH", state / "scoped-change-leases.json")
    monkeypatch.setattr(SCOPED, "GLOBAL_LEASE_PATH", state / "control-tower-change-lock.json")
    monkeypatch.setattr(SCOPED, "LIFECYCLE_LOCK_PATH", state / "agent-source-lifecycle.lock")
    monkeypatch.setattr(SCOPED, "CLOSEOUT_DIR", state / "agent-source-closeouts")
    monkeypatch.setattr(SCOPED, "EVIDENCE_DIR", state / "scoped-change-evidence")
    monkeypatch.setattr(SCOPED, "TASKS_PATH", tasks)
    return state


def begin(repo: Path, task: str, scope: str) -> str:
    suffix = task[-1]
    SCOPED.begin(
        agent="joshex", objective=f"edit {scope}", task_id=task,
        work_id=f"work-{suffix}", run_id=f"run-{suffix}", repo=repo, scopes=[scope],
    )
    leases = SCOPED.registry()["leases"]
    return next(row["token"] for row in leases if row["taskBinding"]["taskId"] == task)


def test_disjoint_worktrees_can_lease_concurrently_and_overlap_is_blocked(tmp_path: Path, isolated) -> None:
    _repo, first, second = make_repo(tmp_path)
    begin(first, "task-a", "src")
    begin(second, "task-b", "docs")
    assert len(SCOPED.registry()["leases"]) == 2
    with pytest.raises(SystemExit, match="scoped lease overlap"):
        SCOPED.begin(
            agent="jaimes", objective="overlap", task_id="task-b", work_id="work-b",
            run_id="run-b", repo=second, scopes=["src/a.py"],
        )


def test_parent_child_scope_overlap_is_symmetric() -> None:
    assert SCOPED.overlaps("src", "src/a.py")
    assert SCOPED.overlaps("src/a.py", "src")
    assert not SCOPED.overlaps("src", "docs")


def test_scoped_path_cannot_cross_a_symlink(tmp_path: Path, isolated) -> None:
    _repo, first, _second = make_repo(tmp_path)
    (first / "src/link").symlink_to(first / "docs", target_is_directory=True)
    with pytest.raises(SystemExit, match="crosses a symlink"):
        SCOPED.begin(
            agent="joshex", objective="escape", task_id="task-a", work_id="work-a",
            run_id="run-a", repo=first, scopes=["src/link"],
        )


def test_prepare_accepts_only_clean_commits_within_scope(tmp_path: Path, isolated) -> None:
    _repo, first, _second = make_repo(tmp_path)
    token = begin(first, "task-a", "src")
    (first / "src/a.py").write_text("a = 2\n")
    command(first, "git", "add", "src/a.py")
    command(first, "git", "commit", "-m", "scoped")
    SCOPED.prepare(token)
    assert SCOPED.registry()["leases"] == []
    evidence = list((isolated / "scoped-change-evidence").glob("*.json"))
    assert json.loads(evidence[0].read_text())["outcome"] == "prepared"


def test_prepare_rejects_committed_paths_outside_scope(tmp_path: Path, isolated) -> None:
    _repo, first, _second = make_repo(tmp_path)
    token = begin(first, "task-a", "src")
    (first / "docs/b.md").write_text("changed\n")
    command(first, "git", "add", "docs/b.md")
    command(first, "git", "commit", "-m", "outside")
    with pytest.raises(SystemExit, match="outside the lease"):
        SCOPED.prepare(token)
    assert len(SCOPED.registry()["leases"]) == 1


def test_global_integration_lease_blocks_new_scoped_work(tmp_path: Path, isolated) -> None:
    _repo, first, _second = make_repo(tmp_path)
    SCOPED.GLOBAL_LEASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCOPED.GLOBAL_LEASE_PATH.write_text('{"agent": "josh2"}')
    with pytest.raises(SystemExit, match="Canonical integration lease"):
        begin(first, "task-a", "src")


def test_clean_abort_writes_terminal_source_receipt(tmp_path: Path, isolated) -> None:
    _repo, first, _second = make_repo(tmp_path)
    token = begin(first, "task-a", "src")
    SCOPED.abort(token)
    receipts = list((isolated / "agent-source-closeouts").glob("*.json"))
    assert json.loads(receipts[0].read_text())["outcome"] == "aborted"


def test_generated_handoffs_are_operational_churn_not_authored_source() -> None:
    assert "docs/handoffs/" in PREFLIGHT.RUNTIME_PREFIXES


def test_immutable_manifest_ignores_unrelated_worktree_dirt(tmp_path: Path) -> None:
    repo, _first, _second = make_repo(tmp_path)
    commit = command(repo, "git", "rev-parse", "HEAD")
    (repo / "shared.txt").write_text("uncommitted unrelated change\n")
    manifest = tmp_path / "bundle.json"
    created = DEPLOY.create(repo, commit, ["src"], manifest, "test-repo")
    assert created["revision"] == commit
    assert [row["path"] for row in created["files"]] == ["src/a.py"]
    assert DEPLOY.verify(repo, manifest)["ok"] is True
    destination = tmp_path / "release"
    DEPLOY.materialize(repo, manifest, destination)
    assert (destination / "src/a.py").read_text() == "a = 1\n"
    assert not (destination / "shared.txt").exists()


def test_manifest_tampering_fails_closed(tmp_path: Path) -> None:
    repo, _first, _second = make_repo(tmp_path)
    manifest = tmp_path / "bundle.json"
    DEPLOY.create(repo, "HEAD", ["src"], manifest, "test-repo")
    payload = json.loads(manifest.read_text())
    payload["files"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload))
    with pytest.raises(SystemExit, match="input mismatch"):
        DEPLOY.verify(repo, manifest)


def test_manifest_rejects_committed_symlinks(tmp_path: Path) -> None:
    repo, _first, _second = make_repo(tmp_path)
    (repo / "src/link").symlink_to("a.py")
    command(repo, "git", "add", "src/link")
    command(repo, "git", "commit", "-m", "symlink")
    with pytest.raises(SystemExit, match="symlinks or submodules"):
        DEPLOY.create(repo, "HEAD", ["src"], tmp_path / "bundle.json", "test-repo")
