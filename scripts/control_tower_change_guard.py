#!/usr/bin/env python3
"""Coordinate safe, auditable edits to the canonical Control Tower source."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path.home() / ".openclaw" / "state"
LOCK_PATH = STATE_DIR / "control-tower-change-lock.json"
BACKUP_ROOT = Path.home() / ".openclaw" / "backups" / "control-tower-changes"
PUSH_POLICY_PATH = ROOT / "config" / "control-tower-push-policy.json"
SOURCE_PATHS = (
    "AGENTS.md",
    ".github/workflows/mission-control-regression.yml",
    ".gitignore",
    "agent-skills",
    "config",
    "schemas",
    "docs/agent-runbooks.md",
    "docs/brain-topic-intake.md",
    "data/agent-route-benchmark-suite.json",
    "data/agent-routing-policy.json",
    "data/model-provider-budgets.json",
    "v2-react/src",
    "v2-react/index.html",
    "vite.config.ts",
    "package.json",
    "package-lock.json",
    "requirements-qa.txt",
    "plugins/inbox-coordinator",
    "hermes-plugins",
    "launchd",
    "tests",
    "scripts/agent_task.py",
    "scripts/agent_delegate.py",
    "scripts/linear_work_intent.py",
    "scripts/agent_route.py",
    "scripts/agent_publish.py",
    "scripts/model_lane.py",
    "scripts/gemini_agent.py",
    "scripts/ecosystem_health_sweep.py",
    "scripts/ecosystem_qa_benchmark.py",
    "scripts/ecosystem_qa_scheduler.py",
    "scripts/ecosystem_qa_supervisor.py",
    "scripts/ecosystem_proposal_ledger.py",
    "scripts/ecosystem_retention.py",
    "scripts/ecosystem_runtime_probe.py",
    "scripts/ecosystem_state_reconciler.py",
    "scripts/inbox_coordinator.py",
    "scripts/brain_media_intake.py",
    "scripts/brain_intake_worker.py",
    "scripts/brain_fixture_suite.py",
    "scripts/brain_gateway_actions.py",
    "scripts/brain_gateway_dispatcher.py",
    "scripts/brain_topic_manager.py",
    "scripts/brain_topic_catalog.py",
    "scripts/brain_topic_watcher.py",
    "scripts/install_ecosystem_qa_schedules.py",
    "scripts/jaimes_control_tower_blackbox_qc.py",
    "scripts/jaimes_openclaw_gateway_launcher.py",
    "scripts/jaimes_telegram_health.py",
    "scripts/jaimes_telegram_fast_ack.py",
    "scripts/jaimes_telegram_fast_ack_launcher.py",
    "scripts/jaimes_work_card.py",
    "scripts/josh_telegram_fast_ack.py",
    "scripts/josh_telegram_callback_action.py",
    "scripts/josh_work_card.py",
    "scripts/objective_quality.py",
    "scripts/refresh_agentic_robinhood_wallet_live.py",
    "scripts/telegram_channel_registry.py",
    "scripts/telegram_gateway_lifecycle.py",
    "scripts/telegram_lifecycle_release.py",
    "scripts/telegram_shadow_fixture.py",
    "scripts/telegram_response_contract_stress.py",
    "scripts/telegram_inbox_qa_monitor.py",
    "scripts/update_mission_control.py",
    "scripts/control_tower_path_guard.py",
    "scripts/control_tower_change_guard.py",
    "scripts/control_tower_work_store.py",
    "scripts/control_tower_foreground.py",
    "scripts/codex_remote_manual_lane.py",
    "scripts/memory_registry.py",
    "scripts/memory_sleep_review.py",
    "scripts/memory_registry_smoke_test.py",
    "scripts/ecosystem_memory_client.py",
    "scripts/open_mission_control_kiosk.sh",
    "scripts/run_sleep_memory_review.sh",
    "scripts/mission_control_kiosk_watchdog.py",
    "scripts/mission_control_regression_check.py",
    "scripts/mission_control_runtime_layout_check.py",
    "scripts/mission_control_visual_canaries.py",
    "scripts/run_mission_control_watchdog.sh",
    "scripts/route_quality_audit.py",
    "scripts/remote_qa_sidecar_ingest.py",
    "scripts/remediate_jaimes_shell_profile.py",
    "scripts/route_contract_benchmark.py",
    "scripts/state_visibility_guard.py",
    "scripts/test_ecosystem_qa_scheduler.py",
    "scripts/test_ecosystem_state_reconciler.py",
    "scripts/test_telegram_approval_extraction.py",
    "scripts/todays_jobs_consistency_watchdog.py",
)
PYTHON_COMPILE_PATHS = (
    "scripts/control_tower_change_guard.py",
    "scripts/control_tower_work_store.py",
    "scripts/codex_remote_manual_lane.py",
    "scripts/ecosystem_health_sweep.py",
    "scripts/ecosystem_qa_benchmark.py",
    "scripts/mission_control_runtime_layout_check.py",
    "scripts/mission_control_visual_canaries.py",
    "scripts/todays_jobs_consistency_watchdog.py",
    "scripts/jaimes_openclaw_gateway_launcher.py",
    "scripts/josh_telegram_fast_ack.py",
    "scripts/jaimes_telegram_fast_ack.py",
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
    "scripts/josh_work_card.py",
    "scripts/jaimes_work_card.py",
    "scripts/ecosystem_qa_scheduler.py",
    "scripts/ecosystem_runtime_probe.py",
    "scripts/refresh_agentic_robinhood_wallet_live.py",
    "scripts/telegram_response_contract_stress.py",
    "scripts/telegram_inbox_qa_monitor.py",
    "scripts/update_mission_control.py",
    "scripts/agent_task.py",
    "scripts/agent_delegate.py",
    "scripts/model_lane.py",
    "scripts/gemini_agent.py",
    "scripts/linear_work_intent.py",
    "hermes-plugins/jaimes-topic17-runtime-owner/__init__.py",
)
LEASE_MINUTES = 45


def standing_push_approval(agent: str) -> dict | None:
    """Return a narrow, checked-in standing approval captured at lease start."""
    try:
        policy = json.loads(PUSH_POLICY_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    entry = policy.get("agents", {}).get(agent, {}) if isinstance(policy, dict) else {}
    if not isinstance(entry, dict) or entry.get("enabled") is not True:
        return None
    if entry.get("authorizedBy") != "Josh" or entry.get("scope") != "validated-control-tower-origin-main":
        return None
    reference = str(entry.get("authorizationRef") or "").strip()
    if not reference:
        return None
    return {
        "reference": reference,
        "recordedAt": iso(),
        "standing": True,
        "scope": entry["scope"],
    }


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(args: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, env=env, check=False)
    if check and proc.returncode:
        if proc.stdout.strip():
            print(proc.stdout.strip(), file=sys.stderr)
        if proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc


def load_lock() -> dict:
    try:
        return json.loads(LOCK_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def lease_expired(payload: dict) -> bool:
    expires = payload.get("expiresAt")
    try:
        return dt.datetime.fromisoformat(str(expires).replace("Z", "+00:00")) <= now()
    except (TypeError, ValueError):
        return True


def process_is_alive(pid: object) -> bool:
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def read_lock() -> dict:
    """Read a lease without silently deleting expired ownership evidence."""
    #JAIMES: expired leases remain inspectable until guarded orphan recovery records evidence.
    return load_lock()


def public_lease(payload: dict | None) -> dict | None:
    """Return operator-visible lease metadata without the bearer token."""
    if not payload:
        return None
    public = {key: value for key, value in payload.items() if key != "token"}
    public["expired"] = lease_expired(payload)
    return public


def source_changes() -> list[str]:
    proc = run(["git", "status", "--porcelain", "--", *SOURCE_PATHS], check=False)
    return [line[3:].strip() for line in proc.stdout.splitlines() if len(line) > 3]


def require_token(token: str) -> dict:
    payload = read_lock()
    if not payload:
        raise SystemExit("No active Control Tower change lease.")
    if payload.get("token") != token:
        raise SystemExit(f"Control Tower is leased by {payload.get('agent', 'another agent')}.")
    return payload


def source_snapshot() -> list[dict[str, object]]:
    """Freeze guarded-path existence so rollback never consults a later path list."""
    return [
        {"path": relative, "existedAtBegin": (ROOT / relative).exists()}
        for relative in SOURCE_PATHS
    ]


def rollback_snapshot(payload: dict) -> list[dict[str, object]]:
    """Validate rollback inputs completely before mutating any source path."""
    backup = Path(str(payload.get("backup") or ""))
    raw = payload.get("sourceSnapshot")
    legacy = not isinstance(raw, list)
    entries = raw if isinstance(raw, list) else [
        {
            "path": relative,
            "existedAtBegin": (backup / relative).exists(),
        }
        for relative in SOURCE_PATHS
    ]
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    problems: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            problems.append("invalid snapshot entry")
            continue
        relative = str(entry.get("path") or "")
        path = Path(relative)
        if not relative or path.is_absolute() or ".." in path.parts or relative in seen:
            problems.append(f"unsafe or duplicate snapshot path: {relative or '<empty>'}")
            continue
        seen.add(relative)
        existed = bool(entry.get("existedAtBegin"))
        backup_source = backup / relative
        target = ROOT / relative
        if existed and not backup_source.exists():
            problems.append(f"missing pre-edit backup: {relative}")
        if legacy and not existed and target.exists():
            problems.append(f"legacy lease cannot prove existing path was newly created: {relative}")
        normalized.append({"path": relative, "existedAtBegin": existed})
    if problems:
        raise SystemExit(json.dumps({
            "ok": False,
            "reason": "rollback prevalidation failed; lease retained",
            "problems": problems,
        }, indent=2))
    return normalized


def begin(agent: str, objective: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    existing = read_lock()
    if existing:
        reason = "expired lease requires safe recovery" if lease_expired(existing) else "leased"
        raise SystemExit(json.dumps({"ok": False, "reason": reason, "lease": public_lease(existing)}, indent=2))
    dirty = source_changes()
    if dirty:
        raise SystemExit(json.dumps({"ok": False, "reason": "canonical source already dirty", "paths": dirty}, indent=2))
    token = uuid.uuid4().hex
    stamp = now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_ROOT / f"{stamp}-{agent}"
    backup.mkdir(parents=True)
    snapshot = source_snapshot()
    for entry in snapshot:
        relative = str(entry["path"])
        source = ROOT / relative
        if not entry["existedAtBegin"]:
            continue
        target = backup / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
    payload = {
        "agent": agent,
        "objective": objective,
        "token": token,
        "startedAt": iso(),
        "expiresAt": iso(now() + dt.timedelta(minutes=LEASE_MINUTES)),
        "ownerPid": os.getppid(),
        "pushApproval": standing_push_approval(agent),
        "baseCommit": run(["git", "rev-parse", "HEAD"]).stdout.strip(),
        "backup": str(backup),
        "sourceSnapshot": snapshot,
        "source": "v2-react",
        "port": 5174,
    }
    LOCK_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"ok": True, "lease": payload}, indent=2))


def status() -> None:
    print(json.dumps({"ok": True, "lease": public_lease(read_lock()), "sourceChanges": source_changes()}, indent=2))


def renew(token: str) -> None:
    payload = require_token(token)
    if lease_expired(payload):
        raise SystemExit("Expired Control Tower change lease requires safe recovery.")
    payload["expiresAt"] = iso(now() + dt.timedelta(minutes=LEASE_MINUTES))
    LOCK_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"ok": True, "lease": public_lease(payload)}, indent=2))


def extend_snapshot(token: str) -> None:
    """Add newly guarded paths to an active immutable lease.

    A long-running governed change can add a new source path to SOURCE_PATHS
    after the lease begins. An absent target proves it did not exist at begin.
    An existing tracked file is accepted only when its exact begin-state can be
    reconstructed from the lease's immutable base commit and copied into the
    lease backup. Untracked files and directories still fail closed.
    """
    payload = require_token(token)
    if lease_expired(payload):
        raise SystemExit("Expired Control Tower change lease requires safe recovery.")
    snapshot = payload.get("sourceSnapshot")
    if not isinstance(snapshot, list):
        raise SystemExit("Legacy lease cannot extend its rollback snapshot safely.")
    seen = {
        str(entry.get("path") or "")
        for entry in snapshot
        if isinstance(entry, dict)
    }
    additions = [relative for relative in SOURCE_PATHS if relative not in seen]
    backup = Path(str(payload.get("backup") or ""))
    base_commit = str(payload.get("baseCommit") or "").strip()
    entries: list[dict[str, object]] = []
    unsafe: list[str] = []
    for relative in additions:
        target = ROOT / relative
        if not target.exists():
            entries.append({"path": relative, "existedAtBegin": False})
            continue
        if not target.is_file() or not base_commit:
            unsafe.append(relative)
            continue
        prior = run(["git", "show", f"{base_commit}:{relative}"], check=False)
        if getattr(prior, "returncode", 1) != 0:
            unsafe.append(relative)
            continue
        backup_target = backup / relative
        backup_target.parent.mkdir(parents=True, exist_ok=True)
        backup_target.write_text(prior.stdout)
        entries.append({"path": relative, "existedAtBegin": True})
    if unsafe:
        raise SystemExit(json.dumps({
            "ok": False,
            "reason": "newly guarded path begin-state cannot be proven from the lease base commit",
            "paths": unsafe,
        }, indent=2))
    snapshot.extend(entries)
    payload["sourceSnapshot"] = snapshot
    LOCK_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({
        "ok": True,
        "extended": len(additions),
        "paths": additions,
        "lease": public_lease(payload),
    }, indent=2))


def verify(token: str) -> None:
    payload = require_token(token)
    if lease_expired(payload):
        raise SystemExit("Expired Control Tower change lease requires safe recovery.")
    env = dict(os.environ)
    env["CONTROL_TOWER_ALLOW_GENERATED"] = "1"
    qa_python = str(ROOT / ".venv-qa" / "bin" / "python") if (ROOT / ".venv-qa" / "bin" / "python").exists() else sys.executable
    # Guard paths may intentionally name files that will be created under the
    # active lease. Compile every present module while allowing an immutable
    # begin snapshot to prove that a newly authored module did not exist yet.
    compile_paths = [path for path in PYTHON_COMPILE_PATHS if (ROOT / path).is_file()]
    checks = [
        ([sys.executable, "scripts/control_tower_path_guard.py"], env),
        ([sys.executable, "scripts/memory_registry_smoke_test.py"], None),
        ([sys.executable, "-m", "py_compile", *compile_paths], None),
        ([qa_python, "-m", "pytest", "-q", "tests", "scripts/test_telegram_approval_extraction.py", "scripts/test_ecosystem_qa_scheduler.py"], None),
        (["npm", "test", "--prefix", "plugins/inbox-coordinator"], None),
        (["npm", "run", "build"], None),
        ([sys.executable, "scripts/update_mission_control.py"], None),
        ([sys.executable, "scripts/mission_control_regression_check.py"], None),
        ([sys.executable, "scripts/ecosystem_qa_benchmark.py", "--route-only"], None),
        ([sys.executable, "scripts/ecosystem_qa_benchmark.py", "--fault-injection", "--no-write"], None),
        ([sys.executable, "scripts/mission_control_runtime_layout_check.py", "--self-test"], None),
        ([sys.executable, "scripts/mission_control_runtime_layout_check.py",
          "--strict-browser", "--strict-visual",
          "--screenshot-path", "/tmp/control-tower-change-guard.png"], None),
        ([sys.executable, "scripts/mission_control_visual_canaries.py"], None),
        # Regenerate once more so the just-written runtime-layout result is the
        # status consumed by the live ecosystem sweep below.
        ([sys.executable, "scripts/update_mission_control.py"], None),
        # This is intentionally the live sweep on the canonical Josh 2.0 host.
        # CI uses fixture-safe structural checks and never attempts host recovery.
        ([sys.executable, "scripts/ecosystem_qa_benchmark.py", "--health-only", "--no-write"], None),
    ]
    results = []
    for command, command_env in checks:
        proc = run(command, check=False, env=command_env)
        results.append({"command": " ".join(command), "ok": proc.returncode == 0})
        if proc.returncode:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
            raise SystemExit(proc.returncode)
    dirty = source_changes()
    if dirty:
        raise SystemExit(json.dumps({"ok": False, "reason": "intentional source must be committed before release", "paths": dirty}, indent=2))
    print(json.dumps({"ok": True, "agent": payload["agent"], "checks": results, "sourceChanges": dirty}, indent=2))


def release_evidence(payload: dict, outcome: str, detail: str) -> Path:
    evidence = Path(payload["backup"]) / "lifecycle-outcome.json"
    evidence.write_text(json.dumps({
        "agent": payload.get("agent"),
        "objective": payload.get("objective"),
        "outcome": outcome,
        "detail": detail,
        "recordedAt": iso(),
        "sourceChanges": source_changes(),
    }, indent=2) + "\n")
    return evidence


def recovery_ready(payload: dict) -> tuple[bool, str]:
    if not lease_expired(payload):
        return False, "lease has not expired"
    if process_is_alive(payload.get("ownerPid")):
        return False, "owner process is still present"
    if source_changes():
        return False, "canonical source has unresolved changes"
    return True, "expired owner is absent and source is clean"


def recover_expired() -> None:
    payload = read_lock()
    if not payload:
        print(json.dumps({"ok": True, "recovered": False, "lease": None}, indent=2))
        return
    allowed, reason = recovery_ready(payload)
    if not allowed:
        raise SystemExit(json.dumps({"ok": False, "reason": reason, "lease": public_lease(payload)}, indent=2))
    evidence = release_evidence(payload, "expired-orphan-recovered", reason)
    LOCK_PATH.unlink(missing_ok=True)
    print(json.dumps({"ok": True, "recovered": True, "evidence": str(evidence)}, indent=2))


def source_changed_since(payload: dict) -> bool:
    base = str(payload.get("baseCommit") or "HEAD")
    changed = run(["git", "diff", "--name-only", f"{base}..HEAD", "--", *SOURCE_PATHS], check=False).stdout.splitlines()
    return bool(changed)


def approve_push(token: str, approval_ref: str) -> None:
    payload = require_token(token)
    if lease_expired(payload):
        raise SystemExit("Expired Control Tower change lease requires safe recovery.")
    if not approval_ref.strip():
        raise SystemExit("An explicit push approval reference is required.")
    payload["pushApproval"] = {"reference": approval_ref.strip(), "recordedAt": iso()}
    LOCK_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"ok": True, "pushApprovalRecorded": True}, indent=2))


def finish(token: str) -> None:
    payload = require_token(token)
    verify(token)
    run(["git", "fetch", "origin"])
    ahead, behind = (int(value) for value in run(["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"]).stdout.split())
    if ahead or behind:
        raise SystemExit(json.dumps({"ok": False, "reason": "commit/push closure incomplete", "ahead": ahead, "behind": behind}, indent=2))
    if source_changed_since(payload) and not payload.get("pushApproval"):
        raise SystemExit(json.dumps({"ok": False, "reason": "explicit push approval is not recorded"}, indent=2))
    evidence = release_evidence(payload, "finished", "verification passed and local main matches origin/main")
    LOCK_PATH.unlink(missing_ok=True)
    print(json.dumps({"ok": True, "released": payload["agent"], "backup": payload["backup"], "evidence": str(evidence)}, indent=2))


def abort(token: str) -> None:
    payload = require_token(token)
    backup = Path(payload["backup"])
    snapshot = rollback_snapshot(payload)
    evidence = release_evidence(payload, "aborted", "source restored from immutable pre-edit snapshot")
    for entry in snapshot:
        relative = str(entry["path"])
        source = backup / relative
        target = ROOT / relative
        if not entry["existedAtBegin"]:
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            continue
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    LOCK_PATH.unlink(missing_ok=True)
    print(json.dumps({"ok": True, "restored": str(backup), "released": payload["agent"], "evidence": str(evidence)}, indent=2))


@contextmanager
def leased_edit(agent: str, objective: str):
    """Finally-style API for automation: aborts its own lease on every exception."""
    begin(agent, objective)
    payload = read_lock()
    token = str(payload["token"])
    try:
        yield token
    except BaseException as exc:
        if read_lock().get("token") == token:
            abort(token)
        raise exc


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("begin")
    start.add_argument("--agent", required=True, choices=["joshex", "josh2", "jaimes", "jain"])
    start.add_argument("--objective", required=True)
    sub.add_parser("status")
    for name in ("renew", "extend-snapshot", "verify", "finish", "abort"):
        command = sub.add_parser(name)
        command.add_argument("--token", required=True)
    approve = sub.add_parser("approve-push")
    approve.add_argument("--token", required=True)
    approve.add_argument("--approval-ref", required=True)
    sub.add_parser("recover-expired")
    args = parser.parse_args()
    if args.command == "begin": begin(args.agent, args.objective)
    elif args.command == "status": status()
    elif args.command == "renew": renew(args.token)
    elif args.command == "extend-snapshot": extend_snapshot(args.token)
    elif args.command == "verify": verify(args.token)
    elif args.command == "finish": finish(args.token)
    elif args.command == "abort": abort(args.token)
    elif args.command == "approve-push": approve_push(args.token, args.approval_ref)
    elif args.command == "recover-expired": recover_expired()


if __name__ == "__main__":
    main()
