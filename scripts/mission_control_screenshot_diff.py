#!/usr/bin/env python3
"""Focused screenshot diff checks for Mission Control.

Captures the Brain Feed / Memory Roadmap panel in desktop and mobile widths,
then compares against checked-in baselines. Use --update-baseline after an
intentional UI change. Data endpoints are fulfilled with deterministic fixture
payloads so the diff catches layout drift, not live-text churn.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.server
import json
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASELINE_DIR = ROOT / "screenshots" / "baselines"
CURRENT_DIR = ROOT / "screenshots" / "current"
DIFF_DIR = ROOT / "screenshots" / "diffs"

VIEWPORTS = {
    "desktop": {"width": 1440, "height": 1200},
    "mobile": {"width": 390, "height": 1200},
}


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
        return


def stable_payloads() -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    dashboard = json.loads((ROOT / "data" / "dashboard-data.json").read_text())
    dashboard["lastUpdated"] = now
    dashboard["focus"] = {"status": "Regression fixture", "context": "Stable screenshot fixture", "updatedAt": now}
    dashboard["codingVisibility"] = {"updatedAt": now}
    dashboard["trackedTasks"] = []
    dashboard["contextWindow"] = {"usedTokens": 120000, "limitTokens": 240000, "pct": 0.5, "model": "gpt-5.5", "status": "green"}
    josh = {
        "agent": "JOSH 2.0",
        "active": True,
        "status": "active",
        "objective": "Regression fixture: Brain Feed primary objective",
        "currentTool": "browser",
        "model": "GPT-5.4",
        "auth": "api",
        "updatedAt": now,
        "checkedAt": now,
        "steps": [{"label": "Verify Brain Feed layout", "status": "active", "tool": "browser"}],
    }
    jaimes = {
        "agent": "JAIMES",
        "active": True,
        "status": "active",
        "objective": "Regression fixture: JAIMES backend objective",
        "currentTool": "code",
        "model": "GPT-5.5",
        "auth": "subscription",
        "updatedAt": now,
        "checkedAt": now,
        "steps": [{"label": "Run regression guard", "status": "active", "tool": "code"}],
    }
    jain = {**josh, "agent": "J.A.I.N", "active": False, "status": "idle", "objective": "Regression fixture idle lane"}
    dashboard["brainFeed"] = josh
    dashboard["agentBrainFeeds"] = {"josh": josh, "jaimes": jaimes, "jain": jain}
    return {
        "brain-feed.json": josh,
        "jaimes-brain-feed.json": jaimes,
        "jain-brain-feed.json": jain,
        "dashboard-data.json": dashboard,
        "agent-comms.json": [],
    }


def start_server(port: int) -> socketserver.TCPServer:
    handler = lambda *a, **kw: QuietHandler(*a, directory=str(ROOT), **kw)  # noqa: E731
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def image_diff_ratio(a_path: Path, b_path: Path, diff_path: Path) -> float:
    with Image.open(a_path).convert("RGB") as a, Image.open(b_path).convert("RGB") as b:
        if a.size != b.size:
            return 1.0
        diff = ImageChops.difference(a, b)
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff.save(diff_path)
        bbox = diff.getbbox()
        if not bbox:
            return 0.0
        nonzero = 0
        pixels = diff.load()
        width, height = diff.size
        for y in range(height):
            for x in range(width):
                if pixels[x, y] != (0, 0, 0):
                    nonzero += 1
        return nonzero / float(width * height)


def capture(update_baseline: bool, port: int, max_diff_ratio: float) -> int:
    for d in (BASELINE_DIR, CURRENT_DIR, DIFF_DIR):
        d.mkdir(parents=True, exist_ok=True)

    cache_key = hashlib.sha1(str(time.time()).encode()).hexdigest()[:10]
    url = f"http://127.0.0.1:{port}/index.html?mode=kiosk&_screenshot={cache_key}"
    failures: list[str] = []

    httpd = start_server(port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for name, viewport in VIEWPORTS.items():
                    page = browser.new_page(viewport=viewport, device_scale_factor=1)
                    for filename, payload in stable_payloads().items():
                        page.route(
                            f"**/data/{filename}**",
                            lambda route, request, payload=payload: route.fulfill(
                                status=200,
                                content_type="application/json",
                                body=json.dumps(payload),
                            ),
                        )
                    page.goto(url, wait_until="networkidle", timeout=30000)
                    page.wait_for_selector("#brain-feed-card", timeout=15000)
                    page.evaluate("""
                        () => {
                            window.scrollTo(0, 0);
                            const card = document.querySelector('#brain-feed-card');
                            if (!card) return;
                            const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
                            const nodes = [];
                            while (walker.nextNode()) nodes.push(walker.currentNode);
                            for (const node of nodes) {
                                if (node.nodeValue && node.nodeValue.trim()) node.nodeValue = 'TEXT';
                            }
                            const style = document.createElement('style');
                            style.textContent = '#brain-feed-card *, #brain-feed-card { transition:none!important; animation:none!important; }';
                            document.head.appendChild(style);
                        }
                    """)
                    locator = page.locator("#brain-feed-card")
                    current = CURRENT_DIR / f"brain-feed-{name}.png"
                    baseline = BASELINE_DIR / f"brain-feed-{name}.png"
                    diff = DIFF_DIR / f"brain-feed-{name}.png"
                    locator.screenshot(path=str(current), animations="disabled")
                    if update_baseline or not baseline.exists():
                        baseline.write_bytes(current.read_bytes())
                        print(f"baseline_updated {baseline.relative_to(ROOT)}")
                    else:
                        ratio = image_diff_ratio(baseline, current, diff)
                        print(f"screenshot_diff {name} ratio={ratio:.5f} max={max_diff_ratio:.5f}")
                        if ratio > max_diff_ratio:
                            failures.append(f"{name} diff {ratio:.5f} > {max_diff_ratio:.5f}")
                    page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
        with contextlib.suppress(Exception):
            httpd.server_close()

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("mission_control_screenshot_diff OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--port", type=int, default=8776)
    parser.add_argument("--max-diff-ratio", type=float, default=0.08)
    args = parser.parse_args()
    return capture(args.update_baseline, args.port, args.max_diff_ratio)


if __name__ == "__main__":
    sys.exit(main())
