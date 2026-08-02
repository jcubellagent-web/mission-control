#!/usr/bin/env python3
"""Coordinate concurrent source edits in isolated Git worktrees.

Scoped leases protect declared repository-relative paths. They are preparation
leases only: successful work is committed on a task branch, then integrated
through the short, exclusive canonical Control Tower change guard.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any


CONTROL_TOWER_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path(os.environ.get("CONTROL_TOWER_STATE_DIR", Path.home() / ".openclaw" / "state"))
REGISTRY_PATH = STATE_DIR / "scoped-change-leases.json"
GLOBAL_LEASE_PATH = STATE_DIR / "control-tower-change-lock.json"
LIFECYCLE_LOCK_PATH = STATE_DIR / "agent-source-lifecycle.lock"
CLOSEOUT_DIR = STATE_DIR / "agent-source-closeouts"
TASKS_PATH = CONTROL_TOWER_ROOT / "data" / "agent-task-queue.json"
EVIDENCE_DIR = STATE_DIR / "scoped-change-evidence"
LEASE_MINUTES = 45
TERMINAL_TASK_STATUSES = {"done", "blocked", "error", "cancelled"}


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(repo: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, cwd=repo, text=True, capture_output=True)
    if check and proc.returncode:
        raise SystemExit((proc.stderr or proc.stdout).strip())
    return proc


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def atomic_json(path: Path, payload: Any, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    os.chmod(temporary, mode)
    temporary.replace(path)


@contextmanager
def lifecycle_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LIFECYCLE_LOCK_PATH.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
        fcntl.flock(handle, fcntl.LOCK_UN)


def task_binding(task: dict[str, Any]) -> dict[str, str]:
    return {
        "taskId": str(task.get("taskId") or task.get("id") or ""),
        "workId": str(task.get("workId") or ""),
        "runId": str(task.get("runId") or ""),
    }


def require_open_source_task(task_id: str, work_id: str, run_id: str) -> dict[str, Any]:
    tasks = read_json(TASKS_PATH, {}).get("tasks", [])
    task = next((row for row in tasks if isinstance(row, dict) and row.get("id") == task_id), None)
    if not task:
        raise SystemExit(f"Scoped source task not found: {task_id}")
    if task.get("status") in TERMINAL_TASK_STATUSES:
        raise SystemExit(f"Scoped source task is already terminal as {task.get('status')}.")
    if task.get("workScope") != "shared-source":
        raise SystemExit("Scoped leases require a task created with --work-scope shared-source.")
    expected = {"taskId": task_id, "workId": work_id, "runId": run_id}
    if task_binding(task) != expected:
        raise SystemExit("Scoped lease task/work/run binding does not match the task ledger.")
    return task


def normalize_scope(value: str) -> str:
    raw = value.strip().replace("\\", "/").strip("/")
    path = PurePosixPath(raw)
    if not raw or raw == "." or path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise SystemExit(f"Unsafe or overly broad scoped lease path: {value!r}")
    return path.as_posix()


def assert_confined(repo: Path, scope: str) -> None:
    current = repo
    for part in PurePosixPath(scope).parts:
        current = current / part
        if current.is_symlink():
            raise SystemExit(f"Scoped lease path crosses a symlink: {scope}")
    try:
        (repo / scope).resolve(strict=False).relative_to(repo.resolve())
    except ValueError as exc:
        raise SystemExit(f"Scoped lease path escapes the repository: {scope}") from exc


def overlaps(left: str, right: str) -> bool:
    a, b = PurePosixPath(left).parts, PurePosixPath(right).parts
    return a[: len(b)] == b or b[: len(a)] == a


def contains(scopes: list[str], path: str) -> bool:
    candidate = PurePosixPath(path).parts
    return any(candidate[: len(PurePosixPath(scope).parts)] == PurePosixPath(scope).parts for scope in scopes)


def registry() -> dict[str, Any]:
    payload = read_json(REGISTRY_PATH, {})
    leases = payload.get("leases") if isinstance(payload, dict) else None
    return {"version": 1, "leases": leases if isinstance(leases, list) else []}


def expired(lease: dict[str, Any]) -> bool:
    try:
        return dt.datetime.fromisoformat(str(lease["expiresAt"]).replace("Z", "+00:00")) <= now()
    except Exception:
        return True


def public(lease: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in lease.items() if key != "token"} | {"expired": expired(lease)}


def git_identity(repo: Path) -> dict[str, str]:
    root = Path(run(repo, ["git", "rev-parse", "--show-toplevel"]).stdout.strip()).resolve()
    if root != repo.resolve():
        raise SystemExit(f"--repo must be the worktree root: {root}")
    git_dir = Path(run(repo, ["git", "rev-parse", "--git-dir"]).stdout.strip())
    common = Path(run(repo, ["git", "rev-parse", "--git-common-dir"]).stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (repo / git_dir).resolve()
    if not common.is_absolute():
        common = (repo / common).resolve()
    branch = run(repo, ["git", "branch", "--show-current"]).stdout.strip()
    head = run(repo, ["git", "rev-parse", "HEAD"]).stdout.strip()
    if not branch or branch == "main" or git_dir == common:
        raise SystemExit("Scoped edits require a linked Git worktree on a non-main task branch.")
    return {"repoRoot": str(root), "gitDir": str(git_dir), "gitCommonDir": str(common), "branch": branch, "baseCommit": head}


def changed_paths(repo: Path, args: list[str]) -> list[str]:
    return [line.strip() for line in run(repo, args).stdout.splitlines() if line.strip()]


def worktree_dirty(repo: Path) -> list[str]:
    rows = run(repo, ["git", "status", "--porcelain", "--untracked-files=all"]).stdout.splitlines()
    paths = []
    for row in rows:
        value = row[3:] if len(row) > 3 else row
        paths.append(value.rsplit(" -> ", 1)[-1])
    return paths


def owner_alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def begin(*, agent: str, objective: str, task_id: str, work_id: str, run_id: str,
          repo: Path, scopes: list[str]) -> None:
    require_open_source_task(task_id, work_id, run_id)
    repo = repo.resolve()
    normalized = sorted(set(normalize_scope(scope) for scope in scopes))
    if not normalized:
        raise SystemExit("At least one --scope is required.")
    for scope in normalized:
        assert_confined(repo, scope)
    identity = git_identity(repo)
    dirty_claims = [path for path in worktree_dirty(repo) if contains(normalized, path)]
    if dirty_claims:
        raise SystemExit(json.dumps({"ok": False, "reason": "claimed paths are already dirty", "paths": dirty_claims}, indent=2))
    binding = {"taskId": task_id, "workId": work_id, "runId": run_id}
    with lifecycle_lock():
        if read_json(GLOBAL_LEASE_PATH, {}):
            raise SystemExit("Canonical integration lease is active; retry after its short critical section closes.")
        payload = registry()
        for lease in payload["leases"]:
            if expired(lease):
                if lease.get("gitCommonDir") == identity["gitCommonDir"] and any(
                    overlaps(left, right) for left in normalized for right in lease.get("scopes", [])
                ):
                    raise SystemExit(json.dumps({"ok": False, "reason": "overlapping expired lease requires recovery", "lease": public(lease)}, indent=2))
                continue
            if lease.get("gitCommonDir") != identity["gitCommonDir"]:
                continue
            collisions = sorted({f"{left} <> {right}" for left in normalized for right in lease.get("scopes", []) if overlaps(left, right)})
            if collisions:
                raise SystemExit(json.dumps({"ok": False, "reason": "scoped lease overlap", "collisions": collisions, "lease": public(lease)}, indent=2))
        token = uuid.uuid4().hex
        lease = {
            "agent": agent, "objective": objective, "token": token,
            "startedAt": iso(), "expiresAt": iso(now() + dt.timedelta(minutes=LEASE_MINUTES)),
            "ownerPid": os.getppid(), "taskBinding": binding, "scopes": normalized,
            **identity,
        }
        payload["leases"].append(lease)
        atomic_json(REGISTRY_PATH, payload)
    print(json.dumps({"ok": True, "lease": lease}, indent=2))


def require_token(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = registry()
    lease = next((row for row in payload["leases"] if row.get("token") == token), None)
    if not lease:
        raise SystemExit("Scoped change lease token is not active.")
    if expired(lease):
        raise SystemExit("Expired scoped lease requires safe recovery.")
    return payload, lease


def status() -> None:
    print(json.dumps({"ok": True, "leases": [public(row) for row in registry()["leases"]]}, indent=2))


def renew(token: str) -> None:
    with lifecycle_lock():
        payload, lease = require_token(token)
        lease["expiresAt"] = iso(now() + dt.timedelta(minutes=LEASE_MINUTES))
        atomic_json(REGISTRY_PATH, payload)
    print(json.dumps({"ok": True, "lease": public(lease)}, indent=2))


def preparation_evidence(lease: dict[str, Any], outcome: str, detail: str, head: str) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"{hashlib.sha256(lease['token'].encode()).hexdigest()}.json"
    atomic_json(path, {
        "version": 1, "outcome": outcome, "detail": detail, "recordedAt": iso(),
        "taskBinding": lease["taskBinding"], "scopes": lease["scopes"],
        "baseCommit": lease["baseCommit"], "headCommit": head,
        "branch": lease["branch"], "repoRoot": lease["repoRoot"],
    })
    return path


def remove_lease(payload: dict[str, Any], token: str) -> None:
    payload["leases"] = [row for row in payload["leases"] if row.get("token") != token]
    atomic_json(REGISTRY_PATH, payload)


def prepare(token: str) -> None:
    with lifecycle_lock():
        payload, lease = require_token(token)
        repo = Path(lease["repoRoot"])
        identity = git_identity(repo)
        if identity["branch"] != lease["branch"]:
            raise SystemExit("Scoped worktree branch changed during the lease.")
        dirty = worktree_dirty(repo)
        if dirty:
            raise SystemExit(json.dumps({"ok": False, "reason": "commit scoped work before prepare", "paths": dirty}, indent=2))
        changed = changed_paths(repo, ["git", "diff", "--name-only", f"{lease['baseCommit']}..HEAD"])
        outside = [path for path in changed if not contains(lease["scopes"], path)]
        if not changed:
            raise SystemExit("Scoped preparation has no committed changes.")
        if outside:
            raise SystemExit(json.dumps({"ok": False, "reason": "commit changed paths outside the lease", "paths": outside}, indent=2))
        head = run(repo, ["git", "rev-parse", "HEAD"]).stdout.strip()
        evidence = preparation_evidence(lease, "prepared", "scoped commit ready for canonical integration", head)
        remove_lease(payload, token)
    print(json.dumps({"ok": True, "preparedCommit": head, "scopes": lease["scopes"], "evidence": str(evidence)}, indent=2))


def source_receipt_path(binding: dict[str, str]) -> Path:
    exact = "|".join(binding[key] for key in ("taskId", "workId", "runId"))
    return CLOSEOUT_DIR / f"{hashlib.sha256(exact.encode()).hexdigest()}.json"


def write_abort_receipt(lease: dict[str, Any], outcome: str, detail: str, evidence: Path) -> Path:
    binding = lease["taskBinding"]
    receipt = source_receipt_path(binding)
    atomic_json(receipt, {
        "version": 1, **binding, "outcome": outcome, "detail": detail,
        "recordedAt": iso(), "baseCommit": lease["baseCommit"],
        "headCommit": lease["baseCommit"], "sourceClean": True,
        "originSynced": False, "evidence": str(evidence),
    })
    return receipt


def abort(token: str) -> None:
    with lifecycle_lock():
        payload, lease = require_token(token)
        repo = Path(lease["repoRoot"])
        head = run(repo, ["git", "rev-parse", "HEAD"]).stdout.strip()
        dirty = worktree_dirty(repo)
        if head != lease["baseCommit"] or dirty:
            raise SystemExit(json.dumps({"ok": False, "reason": "scoped work is not clean at its base; preserve or restore it before abort", "head": head, "baseCommit": lease["baseCommit"], "paths": dirty}, indent=2))
        evidence = preparation_evidence(lease, "aborted", "clean scoped worktree released at base", head)
        receipt = write_abort_receipt(lease, "aborted", "clean scoped worktree released at base", evidence)
        remove_lease(payload, token)
    print(json.dumps({"ok": True, "released": lease["agent"], "evidence": str(evidence), "receipt": str(receipt)}, indent=2))


def recover_expired() -> None:
    recovered = []
    with lifecycle_lock():
        payload = registry()
        keep = []
        for lease in payload["leases"]:
            if not expired(lease) or owner_alive(lease.get("ownerPid")):
                keep.append(lease)
                continue
            repo = Path(str(lease.get("repoRoot") or ""))
            try:
                head = run(repo, ["git", "rev-parse", "HEAD"]).stdout.strip()
                clean = not worktree_dirty(repo) and head == lease.get("baseCommit")
            except Exception:
                clean = False
            if not clean:
                keep.append(lease)
                continue
            evidence = preparation_evidence(lease, "expired-orphan-recovered", "expired owner absent and scoped worktree clean at base", head)
            receipt = write_abort_receipt(lease, "expired-orphan-recovered", "expired owner absent and scoped worktree clean at base", evidence)
            recovered.append({"taskBinding": lease["taskBinding"], "evidence": str(evidence), "receipt": str(receipt)})
        payload["leases"] = keep
        atomic_json(REGISTRY_PATH, payload)
    print(json.dumps({"ok": True, "recovered": recovered, "remaining": len(payload["leases"])}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("begin")
    start.add_argument("--agent", required=True, choices=("joshex", "josh2", "jaimes", "jain"))
    start.add_argument("--objective", required=True)
    start.add_argument("--task-id", required=True)
    start.add_argument("--work-id", required=True)
    start.add_argument("--run-id", required=True)
    start.add_argument("--repo", required=True, type=Path)
    start.add_argument("--scope", action="append", required=True)
    sub.add_parser("status")
    for name in ("renew", "prepare", "abort"):
        command = sub.add_parser(name)
        command.add_argument("--token", required=True)
    sub.add_parser("recover-expired")
    args = parser.parse_args()
    if args.command == "begin":
        begin(agent=args.agent, objective=args.objective, task_id=args.task_id,
              work_id=args.work_id, run_id=args.run_id, repo=args.repo, scopes=args.scope)
    elif args.command == "status":
        status()
    elif args.command == "renew":
        renew(args.token)
    elif args.command == "prepare":
        prepare(args.token)
    elif args.command == "abort":
        abort(args.token)
    else:
        recover_expired()


if __name__ == "__main__":
    main()
