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


def run(command: list[str], *, cwd: Path | None = None, timeout: int = 120) -> dict[str, Any]:
    try:
        result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "command": command, "detail": compact(str(exc))}
    return {
        "ok": result.returncode == 0,
        "command": command,
        "code": result.returncode,
        "detail": compact((result.stdout or "") + (result.stderr or "")),
    }


def git(source: Path, *args: str) -> dict[str, Any]:
    return run(["git", *args], cwd=source)


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
    manifest = {
        "version": 1,
        "createdAt": now(),
        "target": resolved,
        "sandbox": str(sandbox),
        "sourceState": state,
        "canaryProfile": config["canaryProfile"],
        "requiredGates": config["requiredGates"],
        "observationMinutes": config["observationMinutes"],
        "promotion": {"automatic": False, "status": "manual-review-required"},
        "rollback": {"productionInstall": config["productionInstall"], "sourceHead": state["head"], "prepared": True},
    }
    path = manifest_path(evidence_dir, resolved)
    write_json(path, manifest)
    return {"manifest": str(path), **manifest}


def verify(manifest: dict[str, Any]) -> dict[str, Any]:
    sandbox = Path(manifest["sandbox"])
    checks = {
        "candidate-worktree": {"ok": sandbox.is_dir()},
        "static-compile": run([sys.executable, "-m", "compileall", "-q", "."], cwd=sandbox, timeout=300),
        "git-diff-check": run(["git", "diff", "--check"], cwd=sandbox),
        # The pipeline never sends traffic itself.  This marks the canary as
        # ready for a separately configured, synthetic command on the host.
        "canary-command": {"ok": True, "detail": f"Ready for synthetic profile: {manifest['canaryProfile']}"},
        "rollback-manifest": {"ok": bool(manifest.get("rollback", {}).get("prepared"))},
        "observation-evidence": {"ok": True, "detail": f"Requires {manifest['observationMinutes']} minutes after manual canary start"},
    }
    checks["source-clean"] = {"ok": bool(manifest.get("sourceState", {}).get("sourceClean"))}
    failures = [name for name in manifest["requiredGates"] if not checks.get(name, {}).get("ok")]
    return {"checkedAt": now(), "target": manifest["target"], "checks": checks, "readyForManualCanary": not failures, "failures": failures, "promotion": "manual-review-required"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "prepare", "verify", "promote"])
    parser.add_argument("--target", help="Locally fetched candidate commit or tag (required for prepare)")
    parser.add_argument("--manifest", help="Prepared candidate manifest (required for verify)")
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
    if not args.manifest:
        parser.error("--manifest is required for verify")
    print(json.dumps(verify(read_json(Path(args.manifest))), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
