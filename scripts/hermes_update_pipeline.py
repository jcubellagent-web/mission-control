#!/usr/bin/env python3
"""Prepare and verify a Hermes candidate without changing production.

This is deliberately a *pre-promotion* tool.  It creates an isolated git
worktree, records dashboard-safe evidence, and refuses production promotion.
The eventual human-reviewed deployment remains in the host release runbook.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "hermes-update-pipeline.json"
DEFAULT_EVIDENCE = ROOT / "data" / "hermes-update-evidence"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compact(value: str, limit: int = 500) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def run(command: list[str], *, cwd: Path | None = None, timeout: int = 120, preserve_lines: bool = False) -> dict[str, Any]:
    try:
        result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "command": command, "detail": compact(str(exc))}
    return {
        "ok": result.returncode == 0,
        "command": command,
        "code": result.returncode,
        "detail": ((result.stdout or "") + (result.stderr or "")).strip() if preserve_lines else compact((result.stdout or "") + (result.stderr or "")),
    }


def git(source: Path, *args: str) -> dict[str, Any]:
    return run(["git", *args], cwd=source, preserve_lines=True)


def source_state(config: dict[str, Any]) -> dict[str, Any]:
    source = Path(config["sourceRepository"])
    head = git(source, "rev-parse", "HEAD")
    dirty = git(source, "status", "--porcelain")
    ignored = set(config.get("ignoredDirtyPaths") or [])
    dirty_paths = []
    for line in dirty["detail"].splitlines():
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if path and path not in ignored:
            dirty_paths.append(path)
    return {
        "source": str(source),
        "head": head["detail"] if head["ok"] else "",
        "sourceAccessible": head["ok"],
        "sourceClean": dirty["ok"] and not dirty_paths,
        "ignoredDirtyPaths": sorted(ignored),
        "unexpectedDirtyPaths": dirty_paths,
        "productionMutation": config.get("productionMutation"),
        "automaticPromotion": bool(config.get("automaticPromotion")),
    }


def resolve_target(source: Path, target: str) -> str:
    result = git(source, "rev-parse", "--verify", f"{target}^{{commit}}")
    if not result["ok"]:
        raise ValueError(f"Target is not a locally available git commit: {target}")
    return result["detail"]


def manifest_path(evidence_dir: Path, target: str) -> Path:
    return evidence_dir / f"candidate-{target[:12]}.json"


def local_patch_commits(source: Path, base_ref: str, head_ref: str) -> list[str]:
    result = git(source, "rev-list", "--reverse", f"{base_ref}..{head_ref}")
    if not result["ok"]:
        raise RuntimeError(f"Could not resolve carried patches: {result['detail']}")
    return [line.strip() for line in result["detail"].splitlines() if line.strip()]


def replay_local_patches(source: Path, sandbox: Path, config: dict[str, Any]) -> dict[str, Any]:
    if not config.get("replayLocalPatches", True):
        return {"ok": True, "status": "disabled", "commitCount": 0, "commits": []}
    commits = local_patch_commits(source, config["localPatchBaseRef"], config.get("localPatchHeadRef", "HEAD"))
    applied: list[str] = []
    for commit in commits:
        result = git(sandbox, "cherry-pick", commit)
        if not result["ok"]:
            abort = git(sandbox, "cherry-pick", "--abort")
            return {
                "ok": False,
                "status": "conflict",
                "commitCount": len(commits),
                "appliedCount": len(applied),
                "failedCommit": commit,
                "detail": compact(result["detail"]),
                "abortOk": abort["ok"],
                "commits": commits,
            }
        applied.append(commit)
    return {"ok": True, "status": "applied", "commitCount": len(commits), "appliedCount": len(applied), "commits": commits}


def prepare(config: dict[str, Any], target: str, evidence_dir: Path) -> dict[str, Any]:
    state = source_state(config)
    if not state["sourceAccessible"]:
        raise RuntimeError("Hermes source repository is unavailable")
    if not state["sourceClean"]:
        raise RuntimeError("Refusing to prepare from a dirty Hermes source repository")
    source = Path(config["sourceRepository"])
    resolved = resolve_target(source, target)
    if resolved == state["head"]:
        raise RuntimeError("Candidate equals the current Hermes source commit")
    sandbox = Path(tempfile.mkdtemp(prefix="hermes-candidate-", dir="/private/tmp"))
    created = git(source, "worktree", "add", "--detach", str(sandbox), resolved)
    if not created["ok"]:
        shutil.rmtree(sandbox, ignore_errors=True)
        raise RuntimeError(f"Could not create candidate worktree: {created['detail']}")
    replay = replay_local_patches(source, sandbox, config)
    manifest = {
        "version": 2,
        "createdAt": now(),
        "target": resolved,
        "sandbox": str(sandbox),
        "sourceState": state,
        "canaryProfile": config["canaryProfile"],
        "requiredGates": config["requiredGates"],
        "canaryCommands": config.get("canaryCommands") or [],
        "observationMinutes": config["observationMinutes"],
        "localPatchReplay": replay,
        "promotion": {"automatic": False, "status": "manual-review-required"},
        "rollback": {"productionInstall": config["productionInstall"], "sourceHead": state["head"], "prepared": True},
    }
    path = manifest_path(evidence_dir, resolved)
    write_json(path, manifest)
    return {"manifest": str(path), **manifest}


def run_canary_commands(manifest: dict[str, Any], sandbox: Path) -> dict[str, Any]:
    commands = manifest.get("canaryCommands") or []
    if not commands:
        return {"ok": False, "detail": "No synthetic canary commands are configured.", "results": []}
    results = []
    for raw in commands:
        if not isinstance(raw, list) or not raw:
            results.append({"ok": False, "detail": "Invalid canary command; expected a non-empty argument list."})
            break
        command = [sys.executable if part == "{python}" else str(part) for part in raw]
        result = run(command, cwd=sandbox, timeout=900)
        results.append(result)
        if not result["ok"]:
            break
    return {"ok": bool(results) and all(item.get("ok") for item in results), "results": results}


def observation_check(manifest: dict[str, Any]) -> dict[str, Any]:
    evidence = manifest.get("observationEvidence")
    required = int(manifest.get("observationMinutes") or 0)
    if not isinstance(evidence, dict):
        return {"ok": False, "status": "pending", "detail": f"Requires {required} minutes of recorded canary observation."}
    duration = int(evidence.get("durationMinutes") or 0)
    checks = evidence.get("checks") if isinstance(evidence.get("checks"), dict) else {}
    required_checks = {"gateway", "telegramDelivery", "cron", "modelRouting", "browser", "controlTower"}
    failed = sorted(name for name in required_checks if checks.get(name) is not True)
    ok = bool(evidence.get("complete")) and duration >= required and not failed
    return {
        "ok": ok,
        "status": "complete" if ok else "failed" if evidence.get("complete") else "pending",
        "durationMinutes": duration,
        "requiredMinutes": required,
        "failedChecks": failed,
        "detail": "Recorded canary observation satisfies all critical-surface gates." if ok else "Canary observation is incomplete or failed.",
    }


def verify(manifest: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    sandbox = Path(manifest["sandbox"])
    replay = manifest.get("localPatchReplay") if isinstance(manifest.get("localPatchReplay"), dict) else {}
    required_gates = manifest.get("requiredGates") if isinstance(manifest.get("requiredGates"), list) else []
    replay_required_by_manifest = "local-patch-replay" in required_gates
    replay_required_by_config = bool(config.get("replayLocalPatches", True)) if isinstance(config, dict) else False
    replay_required = replay_required_by_manifest or replay_required_by_config
    replay_ok = bool(replay.get("ok")) and (not replay_required or replay.get("status") == "applied")
    checks = {
        "candidate-worktree": {"ok": sandbox.is_dir()},
        "static-compile": run([sys.executable, "-m", "compileall", "-q", "."], cwd=sandbox, timeout=300),
        "git-diff-check": run(["git", "diff", "--check"], cwd=sandbox),
        "local-patch-replay": {
            "ok": replay_ok,
            "status": replay.get("status") or "missing",
            "detail": replay.get("detail", "") or ("Required carried patches were not applied." if replay_required and not replay_ok else ""),
        },
        "canary-command": run_canary_commands(manifest, sandbox) if replay_ok else {"ok": False, "detail": "Skipped because required carried patches did not replay cleanly."},
        "rollback-manifest": {"ok": bool(manifest.get("rollback", {}).get("prepared"))},
        "observation-evidence": observation_check(manifest),
    }
    checks["source-clean"] = {"ok": bool(manifest.get("sourceState", {}).get("sourceClean"))}
    failures = [name for name in required_gates if not checks.get(name, {}).get("ok")]
    if replay_required_by_config and not replay_required_by_manifest:
        failures.append("local-patch-replay-policy")
    pre_observation_failures = [name for name in failures if name != "observation-evidence"]
    return {
        "checkedAt": now(),
        "target": manifest["target"],
        "checks": checks,
        "readyForObservation": not pre_observation_failures,
        "readyForPromotionReview": not failures,
        "failures": failures,
        "promotion": "manual-review-required",
    }


def record_observation(manifest_path_value: Path, evidence_path: Path) -> dict[str, Any]:
    manifest = read_json(manifest_path_value)
    evidence = read_json(evidence_path)
    if evidence.get("target") != manifest.get("target"):
        raise RuntimeError("Observation evidence target does not match the candidate manifest")
    manifest["observationEvidence"] = evidence
    manifest["observationRecordedAt"] = now()
    write_json(manifest_path_value, manifest)
    return observation_check(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "prepare", "verify", "record-observation", "promote"])
    parser.add_argument("--target", help="Locally fetched candidate commit or tag (required for prepare)")
    parser.add_argument("--manifest", help="Prepared candidate manifest (required for verify)")
    parser.add_argument("--observation-evidence", help="Dashboard-safe observation evidence JSON (required for record-observation)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    config = read_json(args.config)
    if args.command == "status":
        print(json.dumps({"checkedAt": now(), **source_state(config)}, indent=2))
        return 0
    if args.command == "promote":
        parser.error("Production promotion is intentionally disabled; use the reviewed host release runbook after canary evidence.")
    if args.command == "prepare":
        if not args.target:
            parser.error("--target is required for prepare")
        print(json.dumps(prepare(config, args.target, args.evidence_dir), indent=2))
        return 0
    if args.command == "record-observation":
        if not args.manifest or not args.observation_evidence:
            parser.error("--manifest and --observation-evidence are required for record-observation")
        print(json.dumps(record_observation(Path(args.manifest), Path(args.observation_evidence)), indent=2))
        return 0
    if not args.manifest:
        parser.error("--manifest is required for verify")
    print(json.dumps(verify(read_json(Path(args.manifest)), config), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
