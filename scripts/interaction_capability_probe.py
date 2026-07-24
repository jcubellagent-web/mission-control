#!/usr/bin/env python3
"""Collect privacy-safe browser and computer-use capability metadata.

The probe never emits screenshots, URLs, page text, selectors, accessibility
trees, cookies, credentials, or raw child-process output. An optional active
display canary captures one temporary frame only to prove Screen Recording
works, records dimensions/latency, and deletes the frame immediately.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "interaction-routing.json"
STATUSES = {"ok", "degraded", "down", "unknown", "not-required"}
FORBIDDEN_KEYS = {
    "accessibilitytree",
    "axtree",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "domsnapshot",
    "pagecontent",
    "pagetext",
    "password",
    "prompt",
    "rawcontent",
    "rawoutput",
    "rawpagetext",
    "screenshot",
    "screenshotbytes",
    "selector",
    "selectors",
    "targeturl",
    "text",
    "token",
    "url",
    "urls",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return int(proc.returncode), (proc.stdout or "") + (proc.stderr or "")
    except Exception:
        return 126, ""


def parse_json(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def compact_version(value: Any) -> str:
    match = re.search(r"\d+(?:\.\d+)+(?:[-+.][A-Za-z0-9.-]+)?", str(value or ""))
    return match.group(0)[:48] if match else ""


def natural_key(value: str) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value))


def latest_plugin_version(name: str) -> str:
    base = Path.home() / ".codex" / "plugins" / "cache" / "openai-bundled" / name
    if not base.is_dir():
        return ""
    versions = [
        row.name
        for row in base.iterdir()
        if row.is_dir() and re.fullmatch(r"\d+(?:\.\d+)+(?:[-+][A-Za-z0-9.-]+)?", row.name)
    ]
    return sorted(versions, key=natural_key)[-1][:48] if versions else ""


def cli_version(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    if not path:
        return {"status": "down", "version": ""}
    code, output = run([path, "--version"], timeout=8)
    return {"status": "ok" if code == 0 else "degraded", "version": compact_version(output)}


def codex_mcp_enabled(name: str) -> bool:
    path = shutil.which("codex")
    if not path:
        return False
    code, output = run([path, "mcp", "list"], timeout=10)
    if code != 0:
        return False
    return any(name in line and "enabled" in line.lower() for line in output.splitlines())


def probe_cdp(port: int = 9222) -> dict[str, Any]:
    started = time.monotonic()
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/json/version", method="GET")
        with urllib.request.urlopen(request, timeout=2) as response:  # nosec B310 - loopback metadata only
            response.read(1)
            ok = 200 <= int(getattr(response, "status", 200)) < 400
    except Exception:
        ok = False
    return {
        "status": "ok" if ok else "down",
        "latencyMs": max(0, round((time.monotonic() - started) * 1000)),
        "port": int(port),
    }


def probe_cua_driver() -> dict[str, Any]:
    path = Path.home() / ".local" / "bin" / "cua-driver"
    if not path.exists():
        return {
            "status": "not-required",
            "version": "",
            "accessibility": False,
            "screenRecording": False,
            "screenCapturable": False,
            "cursorOverlay": False,
            "pictureInPicture": False,
            "width": 0,
            "height": 0,
        }
    status_code, status_output = run([str(path), "status"], timeout=8)
    permissions_code, permissions_output = run([str(path), "permissions", "status", "--json"], timeout=10)
    size_code, size_output = run([str(path), "call", "get_screen_size", "{}"], timeout=10)
    version_code, version_output = run([str(path), "--version"], timeout=6)
    config_code, config_output = run([str(path), "config", "show", "--json"], timeout=8)
    permissions = parse_json(permissions_output) if permissions_code == 0 else {}
    size = parse_json(size_output) if size_code == 0 else {}
    config = parse_json(config_output) if config_code == 0 else {}
    cursor = config.get("agent_cursor") if isinstance(config.get("agent_cursor"), dict) else {}
    capturable = permissions.get("screen_recording_capturable")
    ready = (
        status_code == 0
        and "running" in status_output.lower()
        and permissions.get("accessibility") is True
        and permissions.get("screen_recording") is True
        and size_code == 0
    )
    return {
        "status": "ok" if ready else "degraded",
        "version": compact_version(version_output) if version_code == 0 else "",
        "accessibility": permissions.get("accessibility") is True,
        "screenRecording": permissions.get("screen_recording") is True,
        # A read-only permissions check intentionally returns null on current
        # CuaDriver builds; preserve that as unknown instead of reporting a
        # false capture failure.
        "screenCapturable": capturable if isinstance(capturable, bool) else None,
        "cursorOverlay": cursor.get("enabled") is True,
        "pictureInPicture": config.get("experimental_pip") is True,
        "width": int(size.get("width") or 0) if isinstance(size.get("width"), (int, float)) else 0,
        "height": int(size.get("height") or 0) if isinstance(size.get("height"), (int, float)) else 0,
    }


def probe_codex_computer_use() -> dict[str, Any]:
    version = latest_plugin_version("computer-use")
    enabled = codex_mcp_enabled("computer-use")
    return {
        "status": "ok" if version and enabled else "degraded" if version else "down",
        "version": version,
        "mcpEnabled": bool(enabled),
    }


def probe_browser() -> dict[str, Any]:
    browser_version = latest_plugin_version("browser")
    chrome_version = latest_plugin_version("chrome")
    agent_browser = cli_version("agent-browser")
    playwright = cli_version("playwright")
    cdp = probe_cdp()
    plugin_ready = bool(browser_version and chrome_version)
    tool_ready = agent_browser.get("status") == "ok" or playwright.get("status") == "ok"
    return {
        "status": "ok" if plugin_ready and tool_ready else "degraded" if plugin_ready or tool_ready else "down",
        "browserPluginVersion": browser_version,
        "chromePluginVersion": chrome_version,
        "agentBrowser": agent_browser,
        "playwright": playwright,
        "cdp": cdp,
    }


def display_online() -> dict[str, Any]:
    profiler = shutil.which("system_profiler") or "/usr/sbin/system_profiler"
    code, output = run([profiler, "SPDisplaysDataType"], timeout=12)
    online = code == 0 and "Online: Yes" in output
    resolution = re.search(r"Resolution:\s*(\d+)\s*x\s*(\d+)", output)
    return {
        "status": "ok" if online else "degraded",
        "online": bool(online),
        "width": int(resolution.group(1)) if resolution else 0,
        "height": int(resolution.group(2)) if resolution else 0,
    }


def active_display_canary() -> dict[str, Any]:
    if platform_name() != "macos":
        return {"status": "not-required", "latencyMs": 0, "width": 0, "height": 0}
    started = time.monotonic()
    handle = tempfile.NamedTemporaryFile(prefix="interaction-canary-", suffix=".png", delete=False)
    path = Path(handle.name)
    handle.close()
    try:
        code, _ = run(["/usr/sbin/screencapture", "-x", str(path)], timeout=10)
        width = height = 0
        if code == 0 and path.exists() and path.stat().st_size > 0:
            sips_code, sips_output = run(["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)], timeout=8)
            if sips_code == 0:
                width_match = re.search(r"pixelWidth:\s*(\d+)", sips_output)
                height_match = re.search(r"pixelHeight:\s*(\d+)", sips_output)
                width = int(width_match.group(1)) if width_match else 0
                height = int(height_match.group(1)) if height_match else 0
        ok = code == 0 and width > 0 and height > 0
        return {
            "status": "ok" if ok else "down",
            "latencyMs": max(0, round((time.monotonic() - started) * 1000)),
            "width": width,
            "height": height,
        }
    finally:
        path.unlink(missing_ok=True)


def platform_name() -> str:
    return "macos" if os.uname().sysname.lower() == "darwin" else os.uname().sysname.lower()


def load_config(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def select_surface(task_semantics: str, host: str, config: dict[str, Any]) -> str:
    node = (config.get("hosts") or {}).get(host) if isinstance(config.get("hosts"), dict) else {}
    node = node if isinstance(node, dict) else {}
    if task_semantics in {"browser-dom", "browser-visual"}:
        return str(node.get("browserSurface") or "unknown")
    if task_semantics == "desktop-ui":
        return str(node.get("desktopSurface") or "unknown")
    if task_semantics == "semantic-operation":
        return "connector-or-api"
    return "unknown"


def sanitize(value: Any) -> Any:
    if isinstance(value, list):
        return [sanitize(row) for row in value[:20]]
    if not isinstance(value, dict):
        return value
    clean: dict[str, Any] = {}
    for key, item in value.items():
        normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
        if normalized in FORBIDDEN_KEYS:
            continue
        clean[str(key)[:64]] = sanitize(item)
    return clean


def collect(host: str, role: str | None, config_path: Path, active_canary: bool = False) -> dict[str, Any]:
    config = load_config(config_path)
    host_config = (config.get("hosts") or {}).get(host) if isinstance(config.get("hosts"), dict) else {}
    host_config = host_config if isinstance(host_config, dict) else {}
    resolved_role = role or str(host_config.get("role") or "unknown")
    browser = probe_browser()
    cua = probe_cua_driver()
    codex_cu = probe_codex_computer_use()
    display = display_online()
    canary = active_display_canary() if active_canary else {"status": "unknown", "latencyMs": 0, "width": 0, "height": 0}
    browser_required = True
    if resolved_role == "headless":
        browser_ready = browser.get("cdp", {}).get("status") == "ok"
        computer_ready = cua.get("status") == "ok"
    else:
        browser_ready = browser.get("status") == "ok"
        computer_ready = codex_cu.get("status") == "ok" and display.get("online") is True
        if active_canary:
            computer_ready = computer_ready and canary.get("status") == "ok"
    overall = "ok" if browser_ready and computer_ready else "degraded" if browser_ready or computer_ready else "down"
    payload = {
        "checkedAt": utc_now(),
        "host": host,
        "role": resolved_role,
        "status": overall,
        "semanticOrder": list(config.get("semanticOrder") or [])[:8],
        "selectedSurfaces": {
            "semanticOperation": select_surface("semantic-operation", host, config),
            "browserDom": select_surface("browser-dom", host, config),
            "browserVisual": select_surface("browser-visual", host, config),
            "desktopUi": select_surface("desktop-ui", host, config),
        },
        "browser": browser,
        "computerUse": {
            "status": cua.get("status") if resolved_role == "headless" else codex_cu.get("status"),
            "codex": codex_cu,
            "cuaDriver": cua,
            "activeDisplayCanary": canary,
        },
        "display": display,
        "displayLease": {
            "required": host_config.get("displayLeaseRequired") is True,
            "headlessCdpRequired": host_config.get("headlessCdpRequired") is True,
        },
        "observability": {
            "cursorOverlay": cua.get("cursorOverlay") is True if resolved_role == "headless" else True,
            "pictureInPicture": cua.get("pictureInPicture") is True,
            "sharedContentTelemetry": False,
            "onHostOnly": True,
        },
        "privacy": {
            "dashboardSafeOnly": True,
            "contentCaptured": False,
            "credentialsCaptured": False,
        },
        "requirements": {
            "browser": browser_required,
            "computerUse": True,
        },
    }
    return sanitize(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe dashboard-safe browser and computer-use capability metadata.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--role", choices=("visible", "headless", "private", "unknown"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--active-display-canary", action="store_true")
    args = parser.parse_args()
    payload = collect(args.host, args.role, args.config, args.active_display_canary)
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
