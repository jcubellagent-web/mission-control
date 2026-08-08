#!/usr/bin/env python3
"""Run bounded, dashboard-safe GLM Cloud advisory reviews."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MODEL_LANE = ROOT / "scripts" / "model_lane.py"
AGENT_PUBLISH = ROOT / "scripts" / "agent_publish.py"
MODEL_RECEIPT = ROOT / "scripts" / "model_lane_receipt.py"
MAX_INPUT_BYTES = 16 * 1024
MAX_ADVISORY_CHARS = 1600
FORBIDDEN_KEY_PARTS = ("credential", "password", "secret", "token", "cookie", "oauth", "email", "account", "connector", "wallet")

WORKFLOWS: dict[str, dict[str, Any]] = {
    "route-qa": {
        "task_type": "technical-analysis",
        "title": "Control Tower route QA advisory",
        "objective": "Review dashboard-safe route quality and Ollama governance metrics. Identify technical routing or telemetry risks and provide advisory-only next actions. Do not propose mutations or access private data.",
    },
    "sorare-prelock": {
        "task_type": "technical-strategy",
        "title": "Sorare pre-lock advisory",
        "objective": "Review a sanitized public Sorare pre-lock snapshot for risk and strategy considerations. This is advisory only: no account access, lineup mutation, submission, or approval authority.",
        "fields": {"schemaVersion", "workflow", "windowLabel", "publicStatSummary", "riskCounts", "candidateCount"},
    },
    "fcc-release-qa": {
        "task_type": "quality-review",
        "title": "Final Card Club release QA advisory",
        "objective": "Review a sanitized Final Card Club release manifest for technical release risks. This is advisory only: no publishing, account access, CI mutation, or approval authority.",
        "fields": {"schemaVersion", "workflow", "releaseLabel", "artifactChecks", "validationSummary", "knownRisks"},
    },
    "fcc-preproduction": {
        "task_type": "content-preproduction",
        "title": "Final Card Club preproduction advisory",
        "objective": "Create a dashboard-safe Final Card Club text preproduction packet from approved facts and constraints. This is advisory only: no source footage access, visual or audio QA, rendering, filesystem mutation, account access, publishing, approval, or final-verification authority.",
        "fields": {"schemaVersion", "workflow", "contentLabel", "approvedFactSummary", "creativeObjective", "platformTargets", "constraints", "knownRisks"},
    },
}


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalized_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def reject_unsafe(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = normalized_key(key)
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                raise ValueError(f"forbidden manifest field: {key}")
            reject_unsafe(child)
    elif isinstance(value, list):
        for child in value:
            reject_unsafe(child)
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise ValueError("manifest values must be JSON primitives, lists, or objects")


def read_manifest(path: Path, workflow: str) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("manifest exceeds maximum size")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    allowed = WORKFLOWS[workflow]["fields"]
    if set(payload) != allowed or payload.get("schemaVersion") != 1 or payload.get("workflow") != workflow:
        raise ValueError("manifest schema is not the exact allowlisted workflow schema")
    reject_unsafe(payload)
    return payload


def route_qa_snapshot() -> dict[str, Any]:
    def load(name: str) -> dict[str, Any]:
        try:
            value = json.loads((DATA / name).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    quality = load("route-quality-audit.json")
    usage = load("modelUsage.json")
    governance = usage.get("ollamaGovernance") if isinstance(usage.get("ollamaGovernance"), dict) else {}
    return {
        "schemaVersion": 1,
        "workflow": "route-qa",
        "routeQuality": {
            "status": str(quality.get("status") or "unknown"),
            "windowRoutes": int(quality.get("windowRoutes") or 0),
            "malformedPrivacySafeIds": int(quality.get("malformedPrivacySafeIds") or 0),
            "invalidJsonLines": int(quality.get("invalidJsonLines") or 0),
            "unsafeRawContentRows": int(quality.get("unsafeRawContentRows") or 0),
            "reasons": [str(item)[:240] for item in quality.get("reasons", [])[:8]],
        },
        "ollamaGovernance": {
            key: governance.get(key)
            for key in ("coveragePct", "nonCanarySuccessPct", "dispositionPct", "stalePendingReceipts", "weeklyRemainingPct", "surplusAlert")
        },
    }


def write_artifact(workflow: str, snapshot: dict[str, Any], output: str) -> Path:
    advisory = " ".join(output.split())[:MAX_ADVISORY_CHARS]
    payload = {
        "schemaVersion": 1,
        "workflow": workflow,
        "model": "glm-5.2:cloud",
        "advisoryOnly": True,
        "generatedAt": iso_now(),
        "inputHash": hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "summary": advisory,
        "guardrails": "No account access, connector access, mutation, approval, or final-verification authority.",
    }
    directory = DATA / "ollama-advisories"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{workflow}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    return target


def publish_parent(work_id: str, run_id: str, workflow: str, *, terminal: str | None = None) -> None:
    """Give every substantive lane an accountable, dashboard-safe controller."""
    is_terminal = terminal is not None
    command = [
        sys.executable, str(AGENT_PUBLISH), "--agent", "joshex",
        "--type", "complete" if terminal == "done" else "status",
        "--title", f"GLM advisory: {workflow}", "--status", terminal or "active",
        "--tool", "ollama_advisory_review.py",
        "--detail", "Bounded dashboard-safe advisory pass; owner retains all execution and approvals.",
        "--privacy", "dashboard-safe", "--brain-feed", "--work-id", work_id, "--run-id", run_id,
        "--work-event", "terminal" if is_terminal else "start",
        "--phase", "complete" if terminal == "done" else "analysis",
        "--model-family", "ollama" if is_terminal else "codex",
        "--model-id", "glm-5.2:cloud" if is_terminal else "gpt-5.6-terra", "--route-verified",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError("advisory controller visibility could not be established")


def mark_receipt_integrated(work_id: str, run_id: str) -> None:
    receipt_id = ""
    receipt_path = DATA / "model-lane-execution-receipts.jsonl"
    try:
        rows = receipt_path.read_text(encoding="utf-8").splitlines()
        for line in reversed(rows):
            row = json.loads(line)
            if row.get("controllerWorkId") == work_id and row.get("controllerRunId") == run_id:
                receipt_id = str(row.get("receiptId") or "")
                break
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    if not receipt_id:
        raise RuntimeError("GLM advisory lane omitted its accountability receipt")
    completed = subprocess.run(
        [sys.executable, str(MODEL_RECEIPT), "disposition", "--receipt-id", receipt_id,
         "--status", "integrated", "--reason-code", "advisory-artifact-written"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("GLM advisory receipt disposition failed")


def run(workflow: str, manifest: Path | None, timeout: int) -> Path:
    snapshot = route_qa_snapshot() if workflow == "route-qa" else read_manifest(manifest or Path(), workflow)
    safe_context = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    cfg = WORKFLOWS[workflow]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dt%H%M%SZ")
    work_id = f"ollama-advisory-{workflow}-{stamp}"
    run_id = f"run-{work_id}"
    fd, result_name = tempfile.mkstemp(prefix=f"{work_id}-", suffix=".txt", dir="/private/tmp")
    os.close(fd)
    result_file = Path(result_name)
    parent_started = False
    try:
        publish_parent(work_id, run_id, workflow)
        parent_started = True
        command = [
            sys.executable, str(MODEL_LANE), "--task-type", cfg["task_type"], "--title", cfg["title"],
            "--objective", cfg["objective"], "--prompt", f"Sanitized input: {safe_context}\nReturn concise, dashboard-safe advisory findings only.",
            "--privacy", "dashboard-safe", "--requester", "joshex", "--controller-work-id", work_id,
            "--controller-run-id", run_id, "--result-file", str(result_file), "--execute",
        ]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)
        if completed.returncode != 0 or not result_file.exists() or not result_file.read_text(encoding="utf-8").strip():
            raise RuntimeError("GLM advisory lane failed closed")
        artifact = write_artifact(workflow, snapshot, result_file.read_text(encoding="utf-8"))
        mark_receipt_integrated(work_id, run_id)
        publish_parent(work_id, run_id, workflow, terminal="done")
        return artifact
    except Exception:
        if parent_started:
            try:
                publish_parent(work_id, run_id, workflow, terminal="error")
            except RuntimeError:
                pass
        raise
    finally:
        result_file.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", choices=sorted(WORKFLOWS), required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if args.workflow != "route-qa" and args.manifest is None:
        parser.error("--manifest is required for event-triggered workflows")
    try:
        artifact = run(args.workflow, args.manifest, max(1, args.timeout))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"ollama advisory rejected: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok", "workflow": args.workflow, "artifact": str(artifact.relative_to(ROOT))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
