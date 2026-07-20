#!/usr/bin/env python3
"""Run one clean, non-streaming Ollama Cloud specialist pass."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def run(model: str, prompt: str, timeout: int) -> str:
    #JAIMES: use the API so thinking traces/terminal control codes never enter integration output.
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError("Ollama Cloud authentication failed") from exc
        raise
    output = str(result.get("response") or "").strip()
    if not output:
        raise RuntimeError("Ollama Cloud returned empty output")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="glm-5.2:cloud")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    prompt = args.prompt or (sys.stdin.read() if not sys.stdin.isatty() else "")
    if not prompt.strip():
        parser.error("a prompt is required")
    try:
        print(run(args.model, prompt, max(1, args.timeout)))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
