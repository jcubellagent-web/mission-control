#!/usr/bin/env python3
"""Run one bounded, non-streaming local Ollama pass without Hermes."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

METRICS_PREFIX = "MODEL_LANE_METRICS:"


def normalize_model(value: str) -> str:
    return str(value or "").strip().lower().removeprefix("ollama/")


def run_with_metrics(model: str, prompt: str, timeout: int) -> tuple[str, dict[str, object]]:
    requested = normalize_model(model)
    if not requested or requested.endswith(":cloud"):
        raise RuntimeError("local Ollama pass requires an exact non-cloud model")
    payload = json.dumps({
        "model": requested,
        "prompt": prompt,
        "stream": False,
        "think": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read())
    actual = normalize_model(result.get("model") or "")
    if actual != requested:
        raise RuntimeError(f"local Ollama returned unexpected model {actual or '<missing>'}")
    output = str(result.get("response") or "").strip()
    if not output:
        raise RuntimeError("local Ollama returned empty output")
    return output, {
        "model": actual,
        "inputTokens": int(result.get("prompt_eval_count") or 0),
        "outputTokens": int(result.get("eval_count") or 0),
        "providerDurationNs": int(result.get("total_duration") or 0),
        "loadDurationNs": int(result.get("load_duration") or 0),
        "doneReason": str(result.get("done_reason") or ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    prompt = args.prompt or (sys.stdin.read() if not sys.stdin.isatty() else "")
    if not prompt.strip():
        parser.error("a prompt is required")
    try:
        output, metrics = run_with_metrics(args.model, prompt, max(1, args.timeout))
        print(output)
        print(f"{METRICS_PREFIX}{json.dumps(metrics, sort_keys=True)}", file=sys.stderr)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
