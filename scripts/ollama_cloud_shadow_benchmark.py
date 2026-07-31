#!/usr/bin/env python3
"""Run a synthetic, metadata-only Ollama Cloud shadow benchmark.

This benchmark never promotes a model. It compares contract compliance and
latency on dashboard-safe synthetic tasks, then writes hashes and metrics only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = ["glm-5.2:cloud", "minimax-m2.7:cloud", "nemotron-3-super:cloud", "gpt-oss:20b-cloud"]
CASES = [
    {"id": "code-review", "task": "Review a synthetic config parser for validation, rollback, and observability risks."},
    {"id": "architecture", "task": "Design a metadata-only execution receipt pipeline with idempotency and failure handling."},
    {"id": "workflow", "task": "Plan a high-volume multi-agent review queue with ownership, heartbeats, and terminal receipts."},
]


def prompt_for(case: dict[str, str]) -> str:
    return (
        "Return JSON only with keys risks (array of strings), recommendations (array of strings), "
        "and confidence (number 0..1). Use no markdown. Synthetic task: " + case["task"]
    )


def run_case(model: str, case: dict[str, str], timeout: int = 240) -> dict[str, Any]:
    payload = json.dumps({"model": model, "prompt": prompt_for(case), "stream": False, "think": False, "options": {"num_predict": 512}}).encode()
    request = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read())
    output = str(result.get("response") or "").strip()
    compliant = False
    try:
        parsed = json.loads(output)
        compliant = isinstance(parsed.get("risks"), list) and isinstance(parsed.get("recommendations"), list) and 0 <= float(parsed.get("confidence")) <= 1
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
        pass
    return {
        "caseId": case["id"],
        "outcome": "success" if output else "error",
        "contractCompliant": compliant,
        "outputSha256": hashlib.sha256(output.encode()).hexdigest() if output else None,
        "inputTokens": int(result.get("prompt_eval_count") or 0),
        "outputTokens": int(result.get("eval_count") or 0),
        "durationMs": round(int(result.get("total_duration") or 0) / 1_000_000),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "ollama-cloud-shadow-benchmark.json")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-total-tokens", type=int, default=12000)
    args = parser.parse_args()
    models = args.models or DEFAULT_MODELS
    rows = []
    consumed_tokens = 0
    for model in models:
        results = []
        for case in CASES:
            if consumed_tokens >= max(1, args.max_total_tokens):
                results.append({"caseId": case["id"], "outcome": "skipped-budget", "contractCompliant": False})
                continue
            try:
                result = run_case(model, case, max(1, args.timeout))
                consumed_tokens += int(result.get("inputTokens") or 0) + int(result.get("outputTokens") or 0)
                results.append(result)
            except Exception as exc:
                results.append({"caseId": case["id"], "outcome": "error", "contractCompliant": False, "errorType": type(exc).__name__})
        rows.append({
            "model": model,
            "status": "shadow-only",
            "cases": results,
            "successRatePct": round(sum(row["outcome"] == "success" for row in results) / len(results) * 100, 1),
            "contractCompliancePct": round(sum(bool(row["contractCompliant"]) for row in results) / len(results) * 100, 1),
        })
    report = {
        "schemaVersion": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "promotionPolicy": "shadow-only; no automatic routing promotion",
        "maxTotalTokens": max(1, args.max_total_tokens),
        "consumedTokens": consumed_tokens,
        "privacy": "synthetic dashboard-safe prompts; metadata and output hashes only",
        "models": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
