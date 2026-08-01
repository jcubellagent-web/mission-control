#!/usr/bin/env python3
"""Stage and verify an exact OpenCLAW package without changing production."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "openclaw-update-pipeline.json"
DEFAULT_EVIDENCE = ROOT / "data" / "openclaw-update-evidence"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compact(value: str, limit: int = 600) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def run(command: list[str], *, cwd: Path | None = None, timeout: int = 180) -> dict[str, Any]:
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


def version_from_output(text: str) -> str | None:
    match = re.search(r"OpenClaw\s+([^\s]+)", text or "", re.IGNORECASE)
    return match.group(1) if match else None


def production_baseline() -> dict[str, Any]:
    version = run(["openclaw", "--version"], timeout=15)
    status = run(["openclaw", "update", "status", "--json"], timeout=45)
    return {
        "ok": bool(version.get("ok")) and bool(status.get("ok")),
        "version": version_from_output(version.get("detail", "")),
        "versionDetail": version.get("detail", ""),
        "updateStatusOk": bool(status.get("ok")),
    }


def reject_prerelease(target: str, config: dict[str, Any]) -> None:
    if config.get("allowPrereleasePromotion"):
        return
    if re.search(r"(?:alpha|beta|rc|dev|preview)", target, re.IGNORECASE):
        raise RuntimeError("Production candidate must be an exact stable OpenCLAW version")


def prepare(config: dict[str, Any], target: str, evidence_dir: Path) -> dict[str, Any]:
    reject_prerelease(target, config)
    baseline = production_baseline()
    if not baseline["ok"] or not baseline.get("version"):
        raise RuntimeError("Refusing to prepare without a healthy production baseline")
    sandbox = Path(tempfile.mkdtemp(prefix="openclaw-candidate-", dir="/private/tmp"))
    package = str(config.get("package") or "openclaw")
    install = run(["npm", "install", "--prefix", str(sandbox), "--ignore-scripts", "--no-audit", "--no-fund", f"{package}@{target}"], timeout=900)
    candidate = sandbox / "node_modules" / ".bin" / "openclaw"
    version = run([str(candidate), "--version"], timeout=30) if install["ok"] and candidate.exists() else {"ok": False, "detail": "Candidate CLI was not installed."}
    installed_version = version_from_output(version.get("detail", ""))
    manifest = {
        "version": 1,
        "createdAt": now(),
        "target": target,
        "sandbox": str(sandbox),
        "candidate": str(candidate),
        "productionBaseline": baseline,
        "candidateInstall": {"ok": bool(install.get("ok")), "detail": install.get("detail", "")},
        "candidateVersion": {"ok": bool(version.get("ok")) and installed_version == target, "installed": installed_version, "detail": version.get("detail", "")},
        "canaryCommands": config.get("canaryCommands") or [],
        "criticalSurfaces": config.get("criticalSurfaces") or [],
        "requiredGates": config["requiredGates"],
        "observationMinutes": int(config["observationMinutes"]),
        "promotion": {"automatic": False, "status": "manual-review-required"},
        "rollback": {"version": baseline["version"], "prepared": True},
    }
    path = evidence_dir / f"candidate-{re.sub(r'[^A-Za-z0-9_.-]', '-', target)}.json"
    write_json(path, manifest)
    return {"manifest": str(path), **manifest}


def synthetic_canary(manifest: dict[str, Any]) -> dict[str, Any]:
    commands = manifest.get("canaryCommands") or []
    candidate = str(manifest.get("candidate") or "")
    if not commands or not candidate:
        return {"ok": False, "detail": "Synthetic canary commands or candidate path are missing.", "results": []}
    results = []
    for raw in commands:
        if not isinstance(raw, list) or not raw:
            results.append({"ok": False, "detail": "Invalid canary command."})
            break
        command = [candidate if part == "{candidate}" else sys.executable if part == "{python}" else str(part) for part in raw]
        result = run(command, cwd=Path(manifest["sandbox"]), timeout=180)
        results.append(result)
        if not result["ok"]:
            break
    return {"ok": bool(results) and all(result.get("ok") for result in results), "results": results}


def observation_check(manifest: dict[str, Any]) -> dict[str, Any]:
    evidence = manifest.get("observationEvidence")
    required_minutes = int(manifest.get("observationMinutes") or 0)
    surfaces = {str(name) for name in manifest.get("criticalSurfaces") or []}
    if not isinstance(evidence, dict):
        return {"ok": False, "status": "pending", "detail": f"Requires {required_minutes} minutes of recorded canary observation."}
    checks = evidence.get("checks") if isinstance(evidence.get("checks"), dict) else {}
    failed = sorted(name for name in surfaces if checks.get(name) is not True)
    duration = int(evidence.get("durationMinutes") or 0)
    ok = bool(evidence.get("complete")) and duration >= required_minutes and not failed
    return {"ok": ok, "status": "complete" if ok else "failed", "durationMinutes": duration, "requiredMinutes": required_minutes, "failedChecks": failed}


def verify(manifest: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "production-baseline": {"ok": bool(manifest.get("productionBaseline", {}).get("ok"))},
        "candidate-install": {"ok": bool(manifest.get("candidateInstall", {}).get("ok"))},
        "candidate-version": {"ok": bool(manifest.get("candidateVersion", {}).get("ok"))},
        "synthetic-canary": synthetic_canary(manifest),
        "observation-evidence": observation_check(manifest),
        "rollback-manifest": {"ok": bool(manifest.get("rollback", {}).get("prepared"))},
    }
    failures = [name for name in manifest["requiredGates"] if not checks.get(name, {}).get("ok")]
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


def record_observation(manifest_path: Path, evidence_path: Path) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    evidence = read_json(evidence_path)
    if evidence.get("target") != manifest.get("target"):
        raise RuntimeError("Observation evidence target does not match the candidate manifest")
    manifest["observationEvidence"] = evidence
    manifest["observationRecordedAt"] = now()
    write_json(manifest_path, manifest)
    return observation_check(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "prepare", "verify", "record-observation", "promote"])
    parser.add_argument("--target")
    parser.add_argument("--manifest")
    parser.add_argument("--observation-evidence")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    config = read_json(args.config)
    if args.command == "status":
        print(json.dumps({"checkedAt": now(), **production_baseline(), "automaticPromotion": False}, indent=2))
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
    print(json.dumps(verify(read_json(Path(args.manifest))), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
