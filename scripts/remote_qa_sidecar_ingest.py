#!/usr/bin/env python3
"""Validate and promote dashboard-safe JAIMES QA sidecars into Control Tower.

Each source is handled independently: a valid source can advance while an
invalid or unavailable source keeps its previous last-good file untouched.
The ingest status contains only source names, fixed validation messages, and
booleans; remote paths and fetched contents are never published there.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
BLACKBOX_OUTPUT = DATA_DIR / "jaimes-control-tower-blackbox.json"
COMPLETION_OUTPUT = DATA_DIR / "jaimes-completion-evidence.json"
STATUS = DATA_DIR / "remote-qa-ingest-status.json"
BLACKBOX_REMOTE = "/Users/jc_agent/.openclaw/workspace/mission-control/data/jaimes-control-tower-blackbox.json"
COMPLETION_REMOTE = "/Users/jc_agent/.openclaw/workspace/mission-control/data/jaimes-completion-evidence.json"

# Backwards-compatible names for callers that imported the original constants.
OUTPUT = BLACKBOX_OUTPUT
REMOTE = BLACKBOX_REMOTE

UTC = dt.timezone.utc
MAX_AGE = dt.timedelta(minutes=30)
MAX_FUTURE_SKEW = dt.timedelta(minutes=2)
MAX_PAYLOAD_BYTES = 64 * 1024
ALLOWED_STATUSES = {"ok", "watch", "attention", "error"}
FORBIDDEN_EXACT = {
    "accountcontent",
    "accountdata",
    "connectorpayload",
    "cookie",
    "customercontent",
    "emailbody",
    "messageid",
    "oauth",
    "objective",
    "password",
    "prompt",
    "rawevidence",
    "rawprompt",
    "runid",
    "secret",
    "sessionid",
    "taskid",
    "telegramchatid",
    "token",
    "workid",
}
COMPLETION_COUNTS = {
    "completedRuns",
    "identityBoundRuns",
    "finalMessagesRequired",
    "finalMessagesLinked",
    "deliveryVerifiedRuns",
    "mismatches",
    "unverifiedCompletions",
    "staleEvidenceDetected",
}
COMPLETION_ISSUES = {
    "no-recent-completed-samples",
    "legacy-or-unbound-completions",
    "missing-final-message-links",
    "unverified-delivery-links",
    "incomplete-current-run-evidence",
    "task-identity-mismatch",
}
COMPLETION_KEYS = {
    "version",
    "owner",
    "privacy",
    "checkedAt",
    "status",
    "ok",
    "scope",
    "issues",
    "contentPolicy",
    *COMPLETION_COUNTS,
}
BLACKBOX_KEYS = {"owner", "team", "privacy", "checkedAt", "status", "ok", "issues", "metrics", "contract"}
BLACKBOX_METRICS = {"latencyMs", "sourceAgeMinutes", "generatedAgeMinutes"}
EXPECTED_TEAM = "Independent Control Tower black-box QA"
EXPECTED_CONTRACT = "Read-only HTTP verification; JAIMES never writes Josh 2.0 canonical dashboard data."
EXPECTED_CONTENT_POLICY = "No task IDs, message IDs, objectives, prompts, account data, or raw evidence leave JAIMES."
SCOPE_PATTERN = re.compile(r"counts-only completed work-card audit over the last ([1-9][0-9]{0,2}) hours")
BLACKBOX_ISSUE_PATTERNS = (
    re.compile(r"live payload is not an object"),
    re.compile(r"Control Tower fetch failed: [A-Za-z_][A-Za-z0-9_]{0,79}"),
    re.compile(r"live payload missing fields: (?:lastUpdated|sourceUpdatedAt|brainFeed|crons|todayJobs|runtimeLayout)(?:, (?:lastUpdated|sourceUpdatedAt|brainFeed|crons|todayJobs|runtimeLayout))*"),
    re.compile(r"source freshness exceeds 5 minutes \((?:unknown|-?[0-9]+(?:\.[0-9]+)?)\)"),
    re.compile(r"dashboard generation exceeds 5 minutes \((?:unknown|-?[0-9]+(?:\.[0-9]+)?)\)"),
    re.compile(r"local-network dashboard latency exceeds 500 ms \(-?[0-9]+(?:\.[0-9]+)?\)"),
)


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


def parse_ts(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def normalized_key(key: Any) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def forbidden_keys(value: Any, found: set[str] | None = None) -> set[str]:
    found = found if found is not None else set()
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            for key, item in current.items():
                normalized = normalized_key(key)
                if normalized in FORBIDDEN_EXACT or normalized.startswith("raw") or normalized.startswith("private"):
                    found.add(str(key))
                pending.append(item)
        elif isinstance(current, list):
            pending.extend(current)
    return found


def validate_common(payload: Any, *, now: dt.datetime | None = None) -> list[str]:
    if not isinstance(payload, dict):
        return ["payload is not an object"]
    issues: list[str] = []
    if payload.get("owner") != "jaimes":
        issues.append("owner must be jaimes")
    if payload.get("privacy") != "dashboard-safe":
        issues.append("privacy must be dashboard-safe")
    current = (now or dt.datetime.now(UTC)).astimezone(UTC)
    stamp = parse_ts(payload.get("checkedAt"))
    if stamp is None:
        issues.append("sidecar checkedAt is missing or invalid")
    else:
        age = current - stamp.astimezone(UTC)
        if age > MAX_AGE:
            issues.append("sidecar is older than 30 minutes")
        elif age < -MAX_FUTURE_SKEW:
            issues.append("sidecar checkedAt is more than 2 minutes in the future")
    unsafe = forbidden_keys(payload)
    if unsafe:
        issues.append("forbidden raw/private-content keys detected")
    status = payload.get("status")
    if status not in ALLOWED_STATUSES:
        issues.append("invalid status")
    if type(payload.get("ok")) is not bool:
        issues.append("ok must be a boolean")
    elif status == "ok" and payload.get("ok") is not True:
        issues.append("status ok requires ok=true")
    elif status in {"watch", "attention", "error"} and payload.get("ok") is not False:
        issues.append(f"status {status} requires ok=false")
    return issues


def is_bounded_number(value: Any, *, minimum: float = 0, maximum: float = 1_000_000) -> bool:
    return type(value) in {int, float} and math.isfinite(float(value)) and minimum <= float(value) <= maximum


def validate_blackbox(payload: Any, *, now: dt.datetime | None = None) -> list[str]:
    issues = validate_common(payload, now=now)
    if not isinstance(payload, dict):
        return issues
    unexpected = sorted(set(payload) - BLACKBOX_KEYS)
    missing = sorted(BLACKBOX_KEYS - set(payload))
    if unexpected:
        issues.append("blackbox contains non-allowlisted fields")
    if missing:
        issues.append("blackbox is missing required fields: " + ", ".join(missing))
    if payload.get("team") != EXPECTED_TEAM:
        issues.append("blackbox team contract is invalid")
    if payload.get("contract") != EXPECTED_CONTRACT:
        issues.append("blackbox read-only contract is invalid")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        issues.append("blackbox metrics must be an object")
    else:
        if set(metrics) != BLACKBOX_METRICS:
            issues.append("blackbox metrics must contain only allowlisted aggregate fields")
        for name in sorted(BLACKBOX_METRICS & set(metrics)):
            value = metrics.get(name)
            if value is not None and not is_bounded_number(value, minimum=-5, maximum=10_000_000):
                issues.append(f"blackbox metric {name} must be a bounded number or null")
    reported = payload.get("issues")
    if not isinstance(reported, list) or any(
        not isinstance(item, str) or not any(pattern.fullmatch(item) for pattern in BLACKBOX_ISSUE_PATTERNS)
        for item in (reported if isinstance(reported, list) else [])
    ):
        issues.append("blackbox issues must use fixed dashboard-safe templates")
    return issues


def validate_completion(payload: Any, *, now: dt.datetime | None = None) -> list[str]:
    issues = validate_common(payload, now=now)
    if not isinstance(payload, dict):
        return issues
    unexpected = sorted(set(payload) - COMPLETION_KEYS)
    missing = sorted(COMPLETION_KEYS - set(payload))
    if unexpected:
        issues.append("completion evidence contains non-allowlisted fields")
    if missing:
        issues.append("completion evidence is missing required fields: " + ", ".join(missing))
    if payload.get("version") != 1:
        issues.append("completion evidence version must be 1")
    scope = payload.get("scope")
    scope_match = SCOPE_PATTERN.fullmatch(scope) if isinstance(scope, str) else None
    if not scope_match or int(scope_match.group(1)) > 720:
        issues.append("completion evidence scope must be a bounded counts-only window")
    if payload.get("contentPolicy") != EXPECTED_CONTENT_POLICY:
        issues.append("completion evidence content policy is invalid")
    counts: dict[str, int] = {}
    for name in sorted(COMPLETION_COUNTS):
        value = payload.get(name)
        if type(value) is not int or not 0 <= value <= 1_000_000:
            issues.append(f"completion aggregate {name} must be a bounded non-negative integer")
        else:
            counts[name] = value
    ordered_names = {
        "completedRuns",
        "identityBoundRuns",
        "finalMessagesRequired",
        "finalMessagesLinked",
        "deliveryVerifiedRuns",
    }
    if ordered_names <= set(counts):
        completed = counts["completedRuns"]
        identity = counts["identityBoundRuns"]
        required = counts["finalMessagesRequired"]
        linked = counts["finalMessagesLinked"]
        verified = counts["deliveryVerifiedRuns"]
        if not (0 <= verified <= linked <= required <= identity <= completed):
            issues.append("completion aggregates violate required count ordering")
        if required != identity:
            issues.append("finalMessagesRequired must equal identityBoundRuns")
        for name in ("mismatches", "unverifiedCompletions", "staleEvidenceDetected"):
            if name in counts and counts[name] > identity:
                issues.append(f"completion aggregate {name} cannot exceed identityBoundRuns")
        if payload.get("ok") is True and any(counts.get(name, 0) for name in ("mismatches", "unverifiedCompletions")):
            issues.append("ok completion evidence cannot contain unverified or mismatched runs")
    reported = payload.get("issues")
    if not isinstance(reported, list):
        issues.append("completion issues must be a list of fixed issue codes")
    elif (
        len(reported) != len(set(reported))
        or any(type(item) is not str or item not in COMPLETION_ISSUES for item in reported)
    ):
        issues.append("completion issues must use unique fixed issue codes")
    return issues


def validate(payload: Any, kind: str = "blackbox", *, now: dt.datetime | None = None) -> list[str]:
    """Validate a sidecar; the default preserves the original API contract."""
    if kind == "blackbox":
        return validate_blackbox(payload, now=now)
    if kind == "completion":
        return validate_completion(payload, now=now)
    return ["unknown sidecar kind"]


def fetch_payload(host: str, remote: str, candidate: Path) -> tuple[Any, list[str]]:
    try:
        proc = subprocess.run(
            ["scp", "-q", f"{host}:{remote}", str(candidate)],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, ["remote fetch timed out"]
    except OSError as exc:
        return None, [f"remote fetch failed: {type(exc).__name__}"]
    if proc.returncode:
        return None, [f"remote fetch failed with exit {proc.returncode}"]
    try:
        if candidate.stat().st_size > MAX_PAYLOAD_BYTES:
            return None, ["remote payload exceeds 64 KiB"]
        return json.loads(candidate.read_text(encoding="utf-8")), []
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        return None, [f"invalid JSON: {type(exc).__name__}"]


def build_specs(
    *,
    data_dir: Path = DATA_DIR,
    blackbox_remote: str = BLACKBOX_REMOTE,
    completion_remote: str = COMPLETION_REMOTE,
) -> tuple[dict[str, Any], ...]:
    return (
        {
            "name": "blackbox",
            "kind": "blackbox",
            "remote": blackbox_remote,
            "output": data_dir / BLACKBOX_OUTPUT.name,
        },
        {
            "name": "completionEvidence",
            "kind": "completion",
            "remote": completion_remote,
            "output": data_dir / COMPLETION_OUTPUT.name,
        },
    )


def ingest_sources(
    host: str,
    specs: tuple[dict[str, Any], ...],
    status_path: Path,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    current = (now or dt.datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    sources: dict[str, Any] = {}
    combined_issues: list[str] = []
    with tempfile.TemporaryDirectory(dir=status_path.parent) as temporary:
        temporary_dir = Path(temporary)
        for spec in specs:
            name = str(spec["name"])
            output = Path(spec["output"])
            candidate = temporary_dir / f"{name}.json"
            payload, issues = fetch_payload(host, str(spec["remote"]), candidate)
            if not issues:
                issues.extend(validate(payload, str(spec["kind"]), now=current))
            promoted = False
            if not issues:
                try:
                    atomic_write(output, payload)
                    promoted = True
                except OSError as exc:
                    issues.append(f"atomic promotion failed: {type(exc).__name__}")
            last_good_preserved = bool(issues and output.exists())
            sources[name] = {
                "ok": not issues,
                "status": "ok" if not issues else "attention",
                "issues": issues,
                "promoted": promoted,
                "lastGoodPreserved": last_good_preserved,
            }
            combined_issues.extend(f"{name}: {issue}" for issue in issues)
    status = {
        "checkedAt": current.isoformat().replace("+00:00", "Z"),
        "ok": not combined_issues,
        "status": "ok" if not combined_issues else "attention",
        "issues": combined_issues,
        "lastGoodPreserved": any(row["lastGoodPreserved"] for row in sources.values()),
        "sources": sources,
    }
    atomic_write(status_path, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("JAIMES_SSH_ALIAS", "jc_agent@100.121.89.84"))
    parser.add_argument("--remote", default=BLACKBOX_REMOTE, help="JAIMES blackbox sidecar path")
    parser.add_argument("--completion-remote", default=COMPLETION_REMOTE, help="JAIMES completion-evidence sidecar path")
    args = parser.parse_args()
    specs = build_specs(blackbox_remote=args.remote, completion_remote=args.completion_remote)
    status = ingest_sources(args.host, specs, STATUS)
    print(json.dumps(status, indent=2))
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
