#!/usr/bin/env python3
"""Dashboard-safe Gemini CLI broker for local agent workflows."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
STATUS_PATH = DATA_DIR / "gemini-ecosystem.json"

SENSITIVE_MARKERS = [
    "api_key",
    "apikey",
    "authorization:",
    "bearer ",
    "client_secret",
    "cookie:",
    "gemini_api_key",
    "oauth",
    "password",
    "private key",
    "refresh_token",
    "secret",
    "token",
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def run(cmd: list[str], timeout: int, stdin_text: str | None = None) -> tuple[int, str, str]:
    def as_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, input=stdin_text)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        return 124, as_text(exc.stdout), as_text(exc.stderr) or "timeout"
    except Exception as exc:
        return 126, "", str(exc)


def cli_status() -> dict[str, Any]:
    path = shutil.which("gemini")
    status: dict[str, Any] = {
        "available": bool(path),
        "path": path or "",
        "version": "",
        "authMode": "Google login or GEMINI_API_KEY",
        "checkedAt": utc_now(),
    }
    if not path:
        status["status"] = "missing"
        return status
    code, out, err = run([path, "--version"], timeout=6)
    text = (out or err).strip()
    status["version"] = text.splitlines()[0] if code == 0 and text else ""
    status["status"] = "installed" if code == 0 else "version-check-failed"
    return status


def prompt_is_sensitive(prompt: str) -> bool:
    lower = prompt.lower()
    return any(marker in lower for marker in SENSITIVE_MARKERS)


def classify_smoke(code: int, out: str, err: str) -> str:
    combined = f"{out}\n{err}".lower()
    if "opening authentication page" in combined or "do you want to continue" in combined:
        return "auth-required"
    if code == 124:
        return "timeout"
    if code == 0 and bool(out.strip()):
        return "pass"
    return "fail"


def update_sidecar(status: dict[str, Any], smoke: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = read_json(STATUS_PATH, {"provider": "Gemini"})
    payload["updatedAt"] = status["checkedAt"]
    payload["localCli"] = {
        "command": "gemini",
        "path": status.get("path", ""),
        "version": status.get("version", ""),
        "authMode": status.get("authMode", "Google login or GEMINI_API_KEY"),
        "status": status.get("status", "unknown"),
    }
    if smoke:
        payload["lastTest"] = smoke
    write_json(STATUS_PATH, payload)
    return payload


def cmd_status(args: argparse.Namespace) -> int:
    status = cli_status()
    payload: dict[str, Any] = {"ok": status.get("status") == "installed", "geminiCli": status}
    if args.write_status:
        payload["sidecar"] = update_sidecar(status).get("localCli", {})
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


def cmd_smoke(args: argparse.Namespace) -> int:
    status = cli_status()
    prompt = args.prompt.strip()
    if not status.get("available"):
        print(json.dumps({"ok": False, "error": "gemini CLI missing", "geminiCli": status}, indent=2))
        return 1
    if not args.allow_private and prompt_is_sensitive(prompt):
        print(json.dumps({"ok": False, "error": "prompt blocked by privacy guardrail"}, indent=2))
        return 2
    cmd = [str(status["path"])]
    if args.model:
        cmd += ["-m", args.model]
    cmd += ["-p", prompt]
    code, out, err = run(cmd, timeout=args.timeout, stdin_text="y\n")
    smoke_status = classify_smoke(code, out, err)
    smoke = {
        "status": smoke_status,
        "checkedAt": utc_now(),
        "cliVersion": status.get("version", ""),
        "model": args.model or "default",
        "role": args.role,
        "privacy": "private-approved" if args.allow_private else "dashboard-safe",
        "promptStored": False,
        "outputStored": False,
        "outputChars": len(out.strip()),
        "stderrChars": len(err.strip()),
        "exitCode": code,
    }
    if args.write_status:
        update_sidecar(status, smoke)
    result = {
        "ok": smoke["status"] == "pass",
        "geminiCli": status,
        "smoke": smoke,
    }
    if args.show_output:
        result["outputPreview"] = " ".join(out.split())[:240]
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dashboard-safe Gemini CLI checks for Mission Control.")
    sub = parser.add_subparsers(dest="command", required=True)

    status_p = sub.add_parser("status", help="Check local Gemini CLI installation.")
    status_p.add_argument("--write-status", action="store_true")
    status_p.set_defaults(func=cmd_status)

    smoke_p = sub.add_parser("smoke", help="Run a dashboard-safe Gemini prompt smoke test.")
    smoke_p.add_argument("--prompt", default="Reply with exactly: GEMINI_OK")
    smoke_p.add_argument("--model", default="")
    smoke_p.add_argument("--role", default="gemini-review")
    smoke_p.add_argument("--timeout", type=int, default=45)
    smoke_p.add_argument("--allow-private", action="store_true")
    smoke_p.add_argument("--show-output", action="store_true")
    smoke_p.add_argument("--write-status", action="store_true")
    smoke_p.set_defaults(func=cmd_smoke)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
