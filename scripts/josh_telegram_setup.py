#!/usr/bin/env python3
"""Apply Josh 2.0 Telegram bot UX settings without printing secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "data" / "josh2-telegram-ux-config.json"
TARGET_CHAT = "6218150306"


def load_local_env() -> None:
    env_path = Path.home() / ".openclaw" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api_call(api_base: str, method: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{api_base}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        return {"ok": False, "error": f"HTTP {exc.code}: {body}"}
    except Exception as exc:  # noqa: BLE001 - setup should report API errors cleanly
        return {"ok": False, "error": str(exc)}


def require_ok(name: str, result: dict) -> None:
    if not result.get("ok"):
        raise RuntimeError(f"{name} failed: {result.get('error') or result}")


def main() -> int:
    load_local_env()
    token = os.environ.get("JOSH_TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Missing JOSH_TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN")

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    api_base = f"https://api.telegram.org/bot{token}"
    commands = [
        {"command": item["command"], "description": item["description"]}
        for item in config["commandMenu"]
    ]
    results = {}
    results["setMyCommands"] = api_call(api_base, "setMyCommands", {"commands": commands})
    require_ok("setMyCommands", results["setMyCommands"])
    results["setChatMenuButton"] = api_call(
        api_base,
        "setChatMenuButton",
        {"chat_id": TARGET_CHAT, "menu_button": {"type": "commands"}},
    )
    require_ok("setChatMenuButton", results["setChatMenuButton"])
    results["setMyShortDescription"] = api_call(
        api_base,
        "setMyShortDescription",
        {"short_description": "Josh 2.0 command center: status, routing, Mission Control, and safe next steps."},
    )
    require_ok("setMyShortDescription", results["setMyShortDescription"])
    results["setMyDescription"] = api_call(
        api_base,
        "setMyDescription",
        {
            "description": (
                "Use Josh 2.0 for iPhone-first agent control. Prefer buttons and editable work cards for safe next steps, "
                "Mission Control checks, overview/daily digests, routing to JOSHeX/JAIMES/J.AI.N, and clear model/status visibility."
            )
        },
    )
    require_ok("setMyDescription", results["setMyDescription"])

    print(json.dumps({"ok": True, "configured": list(results), "commands": [c["command"] for c in commands]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
