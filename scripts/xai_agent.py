#!/usr/bin/env python3
"""Dashboard-safe xAI/Grok broker for local agent workflows.

Keys are intentionally read only from local environment variables or private
host files. This script never accepts API keys as command-line arguments.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
STATUS_PATH = DATA_DIR / "xai-ecosystem.json"
DEFAULT_SECRET_ENV = Path.home() / ".openclaw" / "secrets" / "xai.env"
LEGACY_SECRET_FILE = Path.home() / ".secrets" / "xai_api_key.txt"
API_BASE = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.20-reasoning"

SENSITIVE_MARKERS = [
    "api_key",
    "apikey",
    "authorization:",
    "bearer ",
    "client_secret",
    "cookie:",
    "oauth",
    "password",
    "private key",
    "refresh_token",
    "secret",
    "token",
    "xai_api_key",
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return values


def env_key_name(name: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in name.upper()).strip("_")
    return f"XAI_API_KEY_{normalized}" if normalized and normalized != "DEFAULT" else "XAI_API_KEY"


def secret_env_path() -> Path:
    return Path(os.environ.get("XAI_SECRETS_FILE") or DEFAULT_SECRET_ENV).expanduser()


def load_api_key(name: str = "default") -> tuple[str, str]:
    primary = env_key_name(name)
    candidates = [primary]
    if primary != "XAI_API_KEY":
        candidates.append("XAI_API_KEY")
    for key in candidates:
        value = os.environ.get(key)
        if value:
            return value.strip(), f"env:{key}"

    env_values = read_env_file(secret_env_path())
    for key in candidates:
        value = env_values.get(key)
        if value:
            return value.strip(), f"file:{secret_env_path().name}:{key}"

    if LEGACY_SECRET_FILE.exists():
        value = LEGACY_SECRET_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value, f"file:{LEGACY_SECRET_FILE.name}"

    return "", "missing"


def key_metadata(name: str = "default") -> dict[str, Any]:
    key, source = load_api_key(name)
    return {
        "name": name,
        "present": bool(key),
        "source": source,
        "suffix": key[-4:] if key else "",
    }


def prompt_is_sensitive(prompt: str) -> bool:
    lower = prompt.lower()
    return any(marker in lower for marker in SENSITIVE_MARKERS)


def api_request(method: str, path: str, api_key: str, payload: dict[str, Any] | None, timeout: int) -> tuple[int, dict[str, Any], dict[str, str]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{API_BASE}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as res:
            text = res.read().decode("utf-8", errors="replace")
            return res.status, json.loads(text or "{}"), dict(res.headers.items())
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(text or "{}")
        except json.JSONDecodeError:
            data = {"error": compact(text, 500)}
        return exc.code, data, dict(exc.headers.items())
    except Exception as exc:
        return 0, {"error": compact(str(exc), 500)}, {}


def recursive_text(value: Any) -> str:
    chunks: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node_type = node.get("type")
            text = node.get("text")
            if isinstance(text, str) and node_type in {None, "output_text", "text"}:
                chunks.append(text)
            for key in ("content", "output", "message"):
                if key in node:
                    walk(node[key])
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return compact(" ".join(chunks), 1200)


def recursive_urls(value: Any) -> list[str]:
    urls: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            url = node.get("url")
            if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in urls:
                urls.append(url)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return urls[:12]


def usage_metadata(value: Any) -> dict[str, Any]:
    usage = value.get("usage") if isinstance(value, dict) else {}
    if not isinstance(usage, dict):
        usage = {}

    def first_number(*keys: str) -> int:
        for key in keys:
            raw = usage.get(key)
            if isinstance(raw, (int, float)):
                return int(raw)
        return 0

    input_tokens = first_number("input_tokens", "prompt_tokens")
    output_tokens = first_number("output_tokens", "completion_tokens")
    total_tokens = first_number("total_tokens") or input_tokens + output_tokens
    cost_raw = usage.get("cost_usd") or usage.get("costUsd") or value.get("cost_usd") or value.get("costUsd") if isinstance(value, dict) else None
    cost_usd = float(cost_raw) if isinstance(cost_raw, (int, float)) else 0.0
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "costUsd": round(cost_usd, 6),
    }


def update_sidecar(status: dict[str, Any], smoke: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = read_json(STATUS_PATH, {"provider": "xAI / Grok"})
    payload["updatedAt"] = utc_now()
    payload["api"] = status
    payload["storagePolicy"] = {
        "store": False,
        "rawPromptStored": False,
        "rawOutputStored": False,
        "dashboardPrivacy": "metadata-only",
    }
    if smoke:
        payload["lastTest"] = smoke
    write_json(STATUS_PATH, payload)
    return payload


def cmd_install_key(args: argparse.Namespace) -> int:
    target = secret_env_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    var_name = env_key_name(args.name)
    print(f"Enter xAI API key for {var_name}. Input will not echo.", file=sys.stderr)
    key = getpass.getpass("xAI API key: ").strip()
    if not key.startswith("xai-"):
        print(json.dumps({"ok": False, "error": "value does not look like an xAI API key"}, indent=2))
        return 2

    existing = read_env_file(target)
    existing[var_name] = key
    lines = [
        "# Local xAI keys for OpenCLAW/Control Tower agents.",
        "# Do not commit this file. Rotate any key pasted into chat.",
    ]
    for name in sorted(existing):
        lines.append(f"{name}={existing[name]}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(json.dumps({"ok": True, "path": str(target), "variable": var_name, "mode": "0600", "suffix": key[-4:]}, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    key, source = load_api_key(args.name)
    status = {
        "available": bool(key),
        "authMode": "XAI_API_KEY bearer token",
        "apiBase": API_BASE,
        "defaultModel": args.model,
        "key": key_metadata(args.name),
        "checkedAt": utc_now(),
    }
    ok = bool(key)
    if args.probe and key:
        code, data, headers = api_request("GET", "/models", key, None, args.timeout)
        ok = code == 200
        status["probe"] = {
            "status": "pass" if ok else "fail",
            "httpStatus": code,
            "modelCount": len(data.get("data", [])) if isinstance(data.get("data"), list) else None,
            "zeroDataRetention": headers.get("x-zero-data-retention", ""),
            "error": "" if ok else compact(data.get("error") or data, 500),
        }
    elif not key:
        status["status"] = "missing-key"
    else:
        status["status"] = "configured"
        status["source"] = source
    if args.write_status:
        update_sidecar(status)
    print(json.dumps({"ok": ok, "xai": status}, indent=2))
    return 0 if ok else 1


def cmd_smoke(args: argparse.Namespace) -> int:
    key, _source = load_api_key(args.name)
    prompt = args.prompt.strip()
    if not key:
        print(json.dumps({"ok": False, "error": "xAI API key missing", "key": key_metadata(args.name)}, indent=2))
        return 1
    if not args.allow_private and prompt_is_sensitive(prompt):
        print(json.dumps({"ok": False, "error": "prompt blocked by privacy guardrail"}, indent=2))
        return 2

    tools = [{"type": tool} for tool in args.tool]
    payload: dict[str, Any] = {
        "model": args.model,
        "input": [
            {
                "role": "system",
                "content": "You are a concise specialist reviewer for a private agent ecosystem. Never ask for or reveal secrets.",
            },
            {"role": "user", "content": prompt},
        ],
        "store": False,
    }
    if tools:
        payload["tools"] = tools
    code, data, headers = api_request("POST", "/responses", key, payload, args.timeout)
    output_text = recursive_text(data)
    urls = recursive_urls(data)
    usage = usage_metadata(data)
    ok = code == 200 and bool(output_text)
    smoke = {
        "status": "pass" if ok else "fail",
        "checkedAt": utc_now(),
        "model": args.model,
        "role": args.role,
        "tools": args.tool,
        "privacy": "private-approved" if args.allow_private else "dashboard-safe",
        "store": False,
        "promptStored": False,
        "outputStored": False,
        "outputChars": len(output_text),
        "sourceCount": len(urls),
        **usage,
        "httpStatus": code,
        "zeroDataRetention": headers.get("x-zero-data-retention", ""),
    }
    if not ok:
        smoke["error"] = compact(data.get("error") or data, 500)
    if args.write_status:
        status = {
            "available": True,
            "authMode": "XAI_API_KEY bearer token",
            "apiBase": API_BASE,
            "defaultModel": args.model,
            "key": key_metadata(args.name),
            "checkedAt": utc_now(),
        }
        update_sidecar(status, smoke)
    result = {"ok": ok, "smoke": smoke}
    if args.show_output:
        result["outputPreview"] = output_text[:500]
        result["sources"] = urls
    print(json.dumps(result, indent=2))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dashboard-safe xAI/Grok checks for Control Tower.")
    sub = parser.add_subparsers(dest="command", required=True)

    install_p = sub.add_parser("install-key", help="Securely prompt for and store a local xAI key.")
    install_p.add_argument("--name", default="default", help="default, josh, jaimes, jain, or another local alias.")
    install_p.set_defaults(func=cmd_install_key)

    status_p = sub.add_parser("status", help="Check local xAI key presence, optionally with API probe.")
    status_p.add_argument("--name", default="default")
    status_p.add_argument("--model", default=DEFAULT_MODEL)
    status_p.add_argument("--probe", action="store_true")
    status_p.add_argument("--timeout", type=int, default=45)
    status_p.add_argument("--write-status", action="store_true")
    status_p.set_defaults(func=cmd_status)

    smoke_p = sub.add_parser("smoke", help="Run a dashboard-safe xAI Responses API smoke test.")
    smoke_p.add_argument("--name", default="default")
    smoke_p.add_argument("--prompt", default="Reply with exactly: XAI_OK")
    smoke_p.add_argument("--model", default=DEFAULT_MODEL)
    smoke_p.add_argument("--role", default="xai-current-events")
    smoke_p.add_argument("--tool", action="append", choices=["web_search", "x_search", "code_interpreter"], default=[])
    smoke_p.add_argument("--timeout", type=int, default=120)
    smoke_p.add_argument("--allow-private", action="store_true")
    smoke_p.add_argument("--show-output", action="store_true")
    smoke_p.add_argument("--write-status", action="store_true")
    smoke_p.set_defaults(func=cmd_smoke)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
