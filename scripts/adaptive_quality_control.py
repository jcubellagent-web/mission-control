#!/usr/bin/env python3
"""Evidence-driven QA/QC and refactor discovery for the agent ecosystem.

The scheduled path is deliberately read-only with respect to source. It writes
dashboard-safe evidence sidecars, ranks bounded refactor proposals, and never
lowers a promoted baseline automatically.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "adaptive-quality-control.json"
OUTPUT_PATH = ROOT / "data" / "adaptive-quality-control.json"
CANDIDATES_PATH = ROOT / "data" / "adaptive-refactor-candidates.json"
BASELINE_PATH = ROOT / "data" / "adaptive-quality-baseline.json"
BASELINE_HISTORY_PATH = ROOT / "data" / "adaptive-quality-baseline-history.json"
SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".mjs", ".sh"}
COMMENT_ONLY = re.compile(r"^\s*(#|//|/\*|\*|\*/|$)")
FUNCTION_LINE = re.compile(r"^\s*(?:async\s+)?(?:def|function|class)\s+|^\s*(?:export\s+)?(?:async\s+)?[A-Za-z_$][\w$]*\s*=\s*(?:async\s*)?\(")


def iso(value: dt.datetime | None = None) -> str:
    return (value or dt.datetime.now(dt.timezone.utc)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def nested_value(payload: Any, dotted: str) -> Any:
    value = payload
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def evaluate_contracts(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for contract in config.get("contracts", []):
        path = ROOT / str(contract.get("path") or "")
        payload = read_json(path, None)
        observed = nested_value(payload, str(contract.get("field") or "")) if payload is not None else None
        accepted = contract.get("accepted") if isinstance(contract.get("accepted"), list) else []
        exists = path.is_file()
        passed = exists and observed in accepted
        required = bool(contract.get("required"))
        state = "pass" if passed else "fail" if required else "watch"
        rows.append({
            "id": str(contract.get("id") or path.stem),
            "label": str(contract.get("label") or contract.get("id") or path.stem),
            "state": state,
            "passed": passed,
            "required": required,
            "weight": max(0, int(contract.get("weight") or 0)),
            "evidence": path.relative_to(ROOT).as_posix(),
            "field": str(contract.get("field") or ""),
            "observed": observed,
        })
    return rows


def source_files(config: dict[str, Any]) -> list[Path]:
    repository = config.get("repository") if isinstance(config.get("repository"), dict) else {}
    extensions = {str(value) for value in repository.get("extensions", SOURCE_EXTENSIONS)}
    excluded = {str(value) for value in repository.get("excludedParts", [])}
    files: set[Path] = set()
    for root_name in repository.get("roots", []):
        root = ROOT / str(root_name)
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in extensions:
                continue
            relative = path.relative_to(ROOT)
            if any(part in excluded for part in relative.parts):
                continue
            files.add(path)
    return sorted(files)


def safe_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def churn_counts() -> Counter[str]:
    try:
        result = subprocess.run(
            ["git", "log", "--since=30 days ago", "--name-only", "--format="],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return Counter()
    return Counter(line.strip() for line in result.stdout.splitlines() if line.strip())


def path_risk(relative: str, config: dict[str, Any]) -> str:
    policy = config.get("riskPolicy") if isinstance(config.get("riskPolicy"), dict) else {}
    lowered = relative.lower()
    if any(fragment.lower() in lowered for fragment in policy.get("protectedPathFragments", [])):
        return "high"
    if relative.startswith(("config/", "agent-skills/")) or relative.endswith(("types.ts", "data.ts")):
        return "medium"
    return "low"


def candidate_id(kind: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join((kind, *parts)).encode("utf-8")).hexdigest()[:12]
    return f"{kind}-{digest}"


def repository_analysis(config: dict[str, Any], *, deep: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    files = source_files(config)
    repository = config.get("repository") if isinstance(config.get("repository"), dict) else {}
    large_threshold = int(repository.get("largeFileLines") or 1200)
    very_large_threshold = int(repository.get("veryLargeFileLines") or 2500)
    max_candidates = int(repository.get("maximumCandidates") or 25)
    churn = churn_counts()
    total_lines = 0
    function_count = 0
    file_rows: list[tuple[Path, str, list[str]]] = []
    candidates: list[dict[str, Any]] = []

    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        lines = safe_lines(path)
        line_count = len(lines)
        total_lines += line_count
        function_count += sum(bool(FUNCTION_LINE.search(line)) for line in lines)
        file_rows.append((path, relative, lines))
        if line_count >= large_threshold:
            changes = int(churn.get(relative, 0))
            impact = min(100, round(line_count / max(1, large_threshold) * 45) + min(35, changes * 4))
            confidence = 92 if line_count >= very_large_threshold else 82
            risk = path_risk(relative, config)
            risk_penalty = {"low": 1.0, "medium": 1.35, "high": 2.0}[risk]
            score = round((impact * confidence / 100) / risk_penalty, 1)
            candidates.append({
                "id": candidate_id("large-file", relative),
                "kind": "large-file",
                "title": f"Decompose {Path(relative).name}",
                "path": relative,
                "risk": risk,
                "score": score,
                "impact": impact,
                "confidence": confidence,
                "evidence": {"lines": line_count, "changes30d": changes},
                "proposal": "Extract cohesive modules behind existing interfaces; preserve behavior with characterization and contract tests.",
                "automaticMutationAllowed": False,
            })

    duplicate_groups: list[dict[str, Any]] = []
    if deep:
        block_lines = max(6, int(repository.get("duplicateBlockLines") or 8))
        fingerprints: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for _path, relative, lines in file_rows:
            normalized = [re.sub(r"\s+", " ", line.strip()) for line in lines]
            for index in range(0, max(0, len(normalized) - block_lines + 1), block_lines):
                block = normalized[index:index + block_lines]
                meaningful = [line for line in block if len(line) >= 12 and not COMMENT_ONLY.match(line)]
                if len(meaningful) < block_lines - 2:
                    continue
                digest = hashlib.sha256("\n".join(meaningful).encode("utf-8")).hexdigest()
                fingerprints[digest].append((relative, index + 1))
        for digest, locations in fingerprints.items():
            distinct_files = sorted({path for path, _line in locations})
            if len(distinct_files) < 2:
                continue
            duplicate_groups.append({"fingerprint": digest[:12], "files": distinct_files[:6], "occurrences": len(locations)})
        duplicate_groups.sort(key=lambda row: (-int(row["occurrences"]), row["fingerprint"]))
        for group in duplicate_groups[:8]:
            risk = "high" if any(path_risk(path, config) == "high" for path in group["files"]) else "medium"
            impact = min(90, 30 + int(group["occurrences"]) * 8 + len(group["files"]) * 6)
            confidence = 72
            score = round((impact * confidence / 100) / ({"medium": 1.35, "high": 2.0}[risk]), 1)
            candidates.append({
                "id": candidate_id("duplication", group["fingerprint"]),
                "kind": "duplication",
                "title": f"Review repeated logic across {len(group['files'])} files",
                "paths": group["files"],
                "risk": risk,
                "score": score,
                "impact": impact,
                "confidence": confidence,
                "evidence": {"occurrences": group["occurrences"], "fingerprint": group["fingerprint"]},
                "proposal": "Confirm semantic equivalence before extracting a shared helper; reject coincidental textual duplication.",
                "automaticMutationAllowed": False,
            })

    risk_order = {"low": 0, "medium": 1, "high": 2}
    candidates.sort(key=lambda row: (-float(row["score"]), risk_order.get(str(row["risk"]), 9), str(row["id"])))
    metrics = {
        "sourceFiles": len(files),
        "sourceLines": total_lines,
        "functionsAndClasses": function_count,
        "largeFiles": sum(len(lines) >= large_threshold for _path, _relative, lines in file_rows),
        "veryLargeFiles": sum(len(lines) >= very_large_threshold for _path, _relative, lines in file_rows),
        "duplicateGroups": len(duplicate_groups),
        "changes30d": sum(churn.values()),
    }
    return metrics, candidates[:max_candidates]


def quality_score(contracts: list[dict[str, Any]]) -> int:
    total = sum(int(row["weight"]) for row in contracts) or 1
    earned = sum(int(row["weight"]) for row in contracts if row["passed"])
    return round(earned / total * 100)


def compare_baseline(metrics: dict[str, Any], score: int, config: dict[str, Any]) -> dict[str, Any]:
    baseline = read_json(BASELINE_PATH, {})
    if not isinstance(baseline, dict) or not baseline.get("promotedAt"):
        return {"status": "candidate", "scoreDelta": None, "message": "No promoted baseline exists yet."}
    baseline_score = int(baseline.get("qualityScore") or 0)
    delta = score - baseline_score
    policy = config.get("baselinePolicy") if isinstance(config.get("baselinePolicy"), dict) else {}
    maximum_regression = int(policy.get("maximumRegressionPoints") or 3)
    status = "attention" if delta < -maximum_regression else "improved" if delta > 0 else "stable"
    return {
        "status": status,
        "scoreDelta": delta,
        "message": "Baseline changes require explicit promotion; scheduled runs never lower it.",
        "promotedAt": baseline.get("promotedAt"),
        "baselineScore": baseline_score,
        "metricDelta": {
            key: int(metrics.get(key) or 0) - int((baseline.get("metrics") or {}).get(key) or 0)
            for key in ("sourceFiles", "sourceLines", "largeFiles", "duplicateGroups")
        },
    }


def build_payload(config: dict[str, Any], mode: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    contracts = evaluate_contracts(config)
    metrics, candidates = repository_analysis(config, deep=mode in {"discover", "baseline-review"})
    score = quality_score(contracts)
    hard_failures = [row for row in contracts if row["required"] and not row["passed"]]
    watches = [row for row in contracts if not row["required"] and not row["passed"]]
    status = "attention" if hard_failures else "watch" if watches or candidates else "ready"
    baseline = compare_baseline(metrics, score, config)
    if baseline.get("status") == "attention":
        status = "attention"
    activities = config.get("recurringActivities") if isinstance(config.get("recurringActivities"), list) else []
    model = config.get("modelPolicy") if isinstance(config.get("modelPolicy"), dict) else {}
    payload = {
        "schemaVersion": 1,
        "checkedAt": iso(),
        "status": status,
        "mode": str(config.get("mode") or "observe-and-propose"),
        "runMode": mode,
        "qualityScore": score,
        "summary": f"{len(contracts) - len(hard_failures)}/{len(contracts)} quality contracts clear; {len(candidates)} bounded refactor candidates ranked.",
        "objective": config.get("qualityObjective"),
        "contracts": contracts,
        "metrics": metrics,
        "refactorPortfolio": {
            "candidates": len(candidates),
            "highRisk": sum(row["risk"] == "high" for row in candidates),
            "mediumRisk": sum(row["risk"] == "medium" for row in candidates),
            "lowRisk": sum(row["risk"] == "low" for row in candidates),
            "automaticSourceMutation": False,
        },
        "baseline": baseline,
        "modelRoute": {
            "analysis": model.get("analysis"),
            "trustedExecutor": model.get("trustedExecutor"),
            "localPrivateFallback": model.get("localPrivateFallback"),
            "independentVerificationRequired": bool(model.get("independentVerificationRequired")),
            "reviewRequiresExactDiffEvidence": bool(model.get("reviewRequiresExactDiffEvidence")),
        },
        "recurringActivities": activities,
        "privacy": {
            "dashboardSafe": True,
            "sourceContentIncluded": False,
            "rawPromptsIncluded": False,
            "secretsIncluded": False,
        },
        "nextAction": (
            f"Investigate required contract {hard_failures[0]['label']}."
            if hard_failures else
            "Review the highest-ranked proposal; implementation remains approval and lease gated."
            if candidates else
            "Continue observation and accumulate clean evidence for the next baseline review."
        ),
    }
    return payload, candidates


def promote_baseline(payload: dict[str, Any], *, replace: bool = False) -> dict[str, Any]:
    if payload.get("status") == "attention":
        raise RuntimeError("refusing to promote a baseline while required quality contracts are failing")
    existing = read_json(BASELINE_PATH, {})
    if isinstance(existing, dict) and existing.get("promotedAt"):
        if not replace:
            raise RuntimeError("a promoted baseline already exists; replacement requires --replace-baseline")
        history = read_json(BASELINE_HISTORY_PATH, {"schemaVersion": 1, "baselines": []})
        if not isinstance(history, dict) or not isinstance(history.get("baselines"), list):
            history = {"schemaVersion": 1, "baselines": []}
        encoded = json.dumps(existing, sort_keys=True, separators=(",", ":")).encode("utf-8")
        history["baselines"].append({
            "archivedAt": iso(),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "baseline": existing,
        })
        atomic_write(BASELINE_HISTORY_PATH, history)
    baseline = {
        "schemaVersion": 1,
        "promotedAt": iso(),
        "qualityScore": payload.get("qualityScore"),
        "metrics": payload.get("metrics"),
        "contractStates": {row["id"]: row["state"] for row in payload.get("contracts", [])},
        "policy": "Manual promotion only; scheduled runs cannot lower or replace this baseline.",
    }
    atomic_write(BASELINE_PATH, baseline)
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--mode", choices=("snapshot", "discover", "baseline-review"), default="snapshot")
    parser.add_argument("--promote-baseline", action="store_true")
    parser.add_argument("--replace-baseline", action="store_true", help="Archive and explicitly replace an existing promoted baseline")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    config = read_json(args.config, {})
    if not isinstance(config, dict) or not config.get("contracts"):
        raise SystemExit(f"invalid adaptive quality config: {args.config}")
    payload, candidates = build_payload(config, args.mode)
    if args.promote_baseline:
        if args.no_write:
            raise SystemExit("--promote-baseline cannot be combined with --no-write")
        payload["baseline"] = {"status": "promoted", **promote_baseline(payload, replace=args.replace_baseline)}
    elif args.replace_baseline:
        raise SystemExit("--replace-baseline requires --promote-baseline")
    if not args.no_write:
        atomic_write(OUTPUT_PATH, payload)
        if args.mode in {"discover", "baseline-review"}:
            atomic_write(CANDIDATES_PATH, {
                "schemaVersion": 1,
                "generatedAt": payload["checkedAt"],
                "mode": "proposal-only",
                "qualityScore": payload["qualityScore"],
                "candidates": candidates,
                "policy": "Candidates are evidence, not authority to mutate source. Shared edit lease and independent verification remain mandatory.",
            })
    print(json.dumps(payload, indent=2))
    return 1 if payload["status"] == "attention" else 0


if __name__ == "__main__":
    raise SystemExit(main())
