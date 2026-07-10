#!/usr/bin/env python3
"""Close completed task tabs while preserving explicitly persistent hosts."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request


DEFAULT_KEEP = {"fun.noxa.fi"}


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=3) as response:
        payload = response.read()
        return json.loads(payload) if payload else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--keep-host", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    keep = DEFAULT_KEEP | {host.lower() for host in args.keep_host}
    base = f"http://127.0.0.1:{args.port}"
    targets = get_json(f"{base}/json/list")
    candidates = []

    for target in targets:
        if target.get("type") != "page":
            continue
        url = target.get("url", "")
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host in keep:
            continue
        candidates.append((target.get("id"), target.get("title", ""), url))

    for target_id, title, url in candidates:
        status = "would-close"
        if args.apply and target_id:
            get_json(f"{base}/json/close/{target_id}")
            status = "closed"
        print(f"{status}\t{title[:80]}\t{url[:160]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
