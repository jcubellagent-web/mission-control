#!/usr/bin/env python3
"""Run one fail-closed Antigravity Gemini specialist pass."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts).strip()
    return ""


def run(model: str, prompt: str, timeout: int) -> str:
    #JAIMES: call the authenticated local subscription proxy directly so a
    # Gemini failure cannot silently consume the GPT fallback pool.
    base_url = os.environ.get("ANTIGRAVITY_BASE_URL", "http://127.0.0.1:11435/v1").rstrip("/")
    token = os.environ.get("ANTIGRAVITY_LOCAL_TOKEN", "agy-local")
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Antigravity Gemini request failed with HTTP {exc.code}") from exc
    actual_model = str(result.get("model") or "").strip()
    if actual_model and actual_model != model:
        raise RuntimeError(f"Antigravity returned unexpected model {actual_model}")
    choices = result.get("choices") or []
    message = (choices[0].get("message") or {}) if choices and isinstance(choices[0], dict) else {}
    output = _content_text(message.get("content"))
    if not output:
        raise RuntimeError("Antigravity Gemini returned empty output")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gemini-3.6-flash-medium")
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
