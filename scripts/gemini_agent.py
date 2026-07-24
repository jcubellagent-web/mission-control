#!/usr/bin/env python3
"""Dashboard-safe Antigravity Gemini broker for local agent workflows."""
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
DEFAULT_MODEL = "gemini-3.6-flash-medium"
REQUIRED_MODELS = {"gemini-3.6-flash-medium", "gemini-3.1-pro-high"}

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


def proxy_status(local: bool) -> dict[str, Any]:
    """Verify the execution host's subscription proxy without exposing auth data."""
    if local:
        command = [
            "curl", "-fsS", "--max-time", "10",
            "http://127.0.0.1:11435/v1/models",
            "-H", "Authorization: Bearer agy-local",
        ]
    else:
        command = [
            "ssh", "jaimes",
            "curl -fsS --max-time 10 http://127.0.0.1:11435/v1/models "
            "-H 'Authorization: Bearer agy-local'",
        ]
    code, out, _err = run(command, timeout=15)
    models: list[str] = []
    if code == 0:
        try:
            payload = json.loads(out)
            models = sorted({
                str(row.get("id") or "").strip()
                for row in payload.get("data", [])
                if isinstance(row, dict) and str(row.get("id") or "").strip()
            })
        except Exception:
            code = 65
    missing = sorted(REQUIRED_MODELS.difference(models))
    available = code == 0 and not missing
    return {
        "available": available,
        "status": "ready" if available else "models-outdated" if code == 0 else "unavailable",
        "executionHost": "JAIMES",
        "modelCount": len(models),
        "requiredModelsPresent": not missing,
        "missingRequiredModels": missing,
        "checkedAt": utc_now(),
    }


def cli_status() -> dict[str, Any]:
    local = Path.home().name == "jc_agent"
    path = shutil.which("agy") if local else "jaimes:/opt/homebrew/bin/agy"
    status: dict[str, Any] = {
        "available": bool(path),
        "path": path or "",
        "version": "",
        "authMode": "Antigravity-authenticated Gemini subscription",
        "accessRoute": "antigravity",
        "transportCommand": "scripts/model_lane.py",
        "displayName": "Antigravity Gemini",
        "legacyName": "google-gemini-cli",
        "checkedAt": utc_now(),
    }
    if not path:
        status["status"] = "missing"
        return status
    if local:
        version_cmd = [str(path), "--version"]
        models_cmd = [str(path), "models"]
    else:
        version_cmd = ["ssh", "jaimes", "/opt/homebrew/bin/agy --version"]
        models_cmd = ["ssh", "jaimes", "/opt/homebrew/bin/agy models"]
    version_code, version_out, version_err = run(version_cmd, timeout=10)
    models_code, models_out, models_err = run(models_cmd, timeout=20)
    version_text = (version_out or version_err).strip()
    models = [line.strip() for line in models_out.splitlines() if line.strip()]
    status["version"] = version_text.splitlines()[0] if version_code == 0 and version_text else ""
    status["models"] = models
    proxy = proxy_status(local)
    status["proxy"] = proxy
    missing = sorted(REQUIRED_MODELS.difference(models))
    if version_code == 0 and models_code == 0 and not missing and proxy["available"]:
        status["status"] = "installed"
    elif models_code != 0:
        status["status"] = "antigravity-auth-required"
        status["warning"] = "Authenticated Antigravity model discovery failed on the JAIMES execution host."
    elif not proxy["available"]:
        status["status"] = "antigravity-proxy-unavailable"
        status["warning"] = "The JAIMES Antigravity subscription proxy is not ready for model-lane execution."
    else:
        status["status"] = "models-outdated"
        status["warning"] = f"Required current models missing: {', '.join(missing)}"
    return status


def prompt_is_sensitive(prompt: str) -> bool:
    lower = prompt.lower()
    return any(marker in lower for marker in SENSITIVE_MARKERS)


def classify_smoke(code: int, out: str, err: str) -> str:
    combined = f"{out}\n{err}".lower()
    if "opening authentication page" in combined or "do you want to continue" in combined:
        return "auth-required"
    if code == 0 and bool(out.strip()):
        return "pass"
    if code == 124 and bool(out.strip()):
        return "pass"
    if code == 124:
        return "timeout"
    return "fail"


def update_sidecar(status: dict[str, Any], smoke: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = read_json(STATUS_PATH, {"provider": "Gemini"})
    payload["updatedAt"] = status["checkedAt"]
    payload["accessRoute"] = "antigravity"
    payload["antigravityGemini"] = {
        "command": "gemini",
        "path": status.get("path", ""),
        "version": status.get("version", ""),
        "authMode": status.get("authMode", "Antigravity-authenticated Gemini subscription"),
        "status": status.get("status", "unknown"),
        "proxyStatus": (status.get("proxy") or {}).get("status", "unknown"),
        "proxyModelCount": (status.get("proxy") or {}).get("modelCount", 0),
        "checkedAt": status.get("checkedAt"),
    }
    payload["localCli"] = {
        "command": "gemini",
        "path": status.get("path", ""),
        "version": status.get("version", ""),
        "authMode": status.get("authMode", "Antigravity-authenticated Gemini subscription"),
        "status": status.get("status", "unknown"),
    }
    if smoke:
        payload["lastTest"] = smoke
    write_json(STATUS_PATH, payload)
    return payload


def cmd_status(args: argparse.Namespace) -> int:
    status = cli_status()
    payload: dict[str, Any] = {"ok": status.get("status") == "installed", "antigravityGemini": status, "geminiCli": status}
    if args.write_status:
        payload["sidecar"] = update_sidecar(status).get("localCli", {})
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


def cmd_smoke(args: argparse.Namespace) -> int:
    status = cli_status()
    prompt = args.prompt.strip()
    if status.get("status") != "installed":
        print(json.dumps({"ok": False, "error": "Antigravity Gemini transport is not ready", "antigravityGemini": status, "geminiCli": status}, indent=2))
        return 1
    if not args.allow_private and prompt_is_sensitive(prompt):
        print(json.dumps({"ok": False, "error": "prompt blocked by privacy guardrail"}, indent=2))
        return 2
    model = args.model or DEFAULT_MODEL
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "model_lane.py"),
        "--task-type",
        args.role,
        "--title",
        "Antigravity Gemini broker probe",
        "--objective",
        "Run one dashboard-safe Gemini specialist pass",
        "--prompt",
        prompt,
        "--privacy",
        "dashboard-safe",
        "--requester",
        "joshex",
        "--requested-provider",
        "gemini",
        "--requested-model",
        model,
        "--requested-reason",
        "Explicit broker smoke test",
        "--lane-visibility",
        "diagnostic",
        "--execute",
    ]
    code, out, err = run(cmd, timeout=args.timeout)
    smoke_status = classify_smoke(code, out, err)
    smoke = {
        "status": smoke_status,
        "checkedAt": utc_now(),
        "cliVersion": status.get("version", ""),
        "model": model,
        "role": args.role,
        "privacy": "private-approved" if args.allow_private else "dashboard-safe",
        "promptStored": False,
        "outputStored": False,
        "outputChars": len(out.strip()),
        "stderrChars": len(err.strip()),
        "exitCode": code,
        "timedOutAfterOutput": code == 124 and bool(out.strip()),
    }
    if args.write_status:
        update_sidecar(status, smoke)
    result = {
        "ok": smoke["status"] == "pass",
        "antigravityGemini": status, "geminiCli": status,
        "smoke": smoke,
    }
    if args.show_output:
        result["outputPreview"] = " ".join(out.split())[:240]
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dashboard-safe Antigravity Gemini checks for Control Tower.")
    sub = parser.add_subparsers(dest="command", required=True)

    status_p = sub.add_parser("status", help="Check Antigravity-backed Gemini installation.")
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
