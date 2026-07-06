#!/usr/bin/env python3
"""Dashboard-safe weekly capability watch for the agent ecosystem."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT = DATA_DIR / "capability-watch.json"
INVENTORY = DATA_DIR / "capability-inventory.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def compact(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "..."


def parse_json_output(out: str) -> Any | None:
    text = (out or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None
    return None


def run(cmd: list[str], timeout: int = 30) -> dict[str, Any]:
    if not shutil.which(cmd[0]):
        return {"ok": False, "status": "missing", "command": cmd[0], "detail": "not installed"}
    try:
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
        out = (proc.stdout or "") + (proc.stderr or "")
        return {"ok": proc.returncode == 0, "status": "ok" if proc.returncode == 0 else "attention", "code": proc.returncode, "detail": compact(out, 600), "json": parse_json_output(out)}
    except Exception as exc:
        return {"ok": False, "status": "attention", "command": cmd[0], "detail": compact(exc, 600)}


def fetch_json(url: str, timeout: int = 12) -> dict[str, Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mission-control-capability-watch"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - public capability metadata
            return {"ok": True, "status": "ok", "data": json.loads(resp.read().decode("utf-8", "replace"))}
    except Exception as exc:
        return {"ok": False, "status": "attention", "detail": compact(exc, 300)}


def npm_dist_tags(package: str) -> dict[str, Any]:
    result = run(["npm", "view", package, "dist-tags", "--json"], timeout=30)
    payload = result.get("json")
    return {
        "ok": result.get("ok"),
        "status": result.get("status"),
        "package": package,
        "distTags": payload if isinstance(payload, dict) else {},
        "detail": result.get("detail"),
    }


def openclaw_status() -> dict[str, Any]:
    result = run(["openclaw", "update", "status", "--json"], timeout=35)
    payload = result.get("json")
    if isinstance(payload, dict):
        return {
            "ok": result.get("ok"),
            "status": "ok" if result.get("ok") else "attention",
            "currentVersion": payload.get("currentVersion") or payload.get("current"),
            "latestVersion": payload.get("latestVersion") or payload.get("latest"),
            "updateAvailable": bool(payload.get("updateAvailable")),
            "channel": payload.get("channel"),
        }
    return {"ok": result.get("ok"), "status": result.get("status"), "detail": result.get("detail")}


def hermes_status() -> dict[str, Any]:
    version = run(["hermes", "--version"], timeout=12)
    update = run(["hermes", "update", "--check"], timeout=45)
    detail = update.get("detail", "")
    up_to_date = "up to date" in detail.lower()
    return {
        "ok": bool(version.get("ok")) and (bool(update.get("ok")) or up_to_date),
        "status": "ok" if up_to_date else "watch" if update.get("ok") else "attention",
        "version": compact(version.get("detail"), 120) if version.get("ok") else "",
        "detail": compact(detail, 260),
    }


def installed_summary(inventory: dict[str, Any]) -> dict[str, Any]:
    nodes = [node for node in inventory.get("nodes", []) if isinstance(node, dict)] if isinstance(inventory, dict) else []
    return {
        "nodes": len(nodes),
        "openclawNodes": sum(1 for node in nodes if isinstance(node.get("openclawCli"), dict) and node["openclawCli"].get("available")),
        "hermesNodes": sum(1 for node in nodes if isinstance(node.get("hermesCli"), dict) and node["hermesCli"].get("available")),
        "geminiReadyNodes": sum(1 for node in nodes if isinstance(node.get("geminiCli"), dict) and node["geminiCli"].get("available")),
        "updatedAt": inventory.get("updatedAt") if isinstance(inventory, dict) else None,
    }


def build_recommendations(sources: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    openclaw = sources.get("openclawUpdate") or {}
    if openclaw.get("updateAvailable"):
        rows.append({
            "id": "openclaw-update-available",
            "status": "upgrade",
            "title": "OpenCLAW update available",
            "detail": f"{openclaw.get('currentVersion') or 'current'} -> {openclaw.get('latestVersion') or 'latest'}",
            "owner": "Josh 2.0 / J.A.I.N",
        })
    hermes = sources.get("hermesUpdate") or {}
    if (
        hermes.get("status") in {"watch", "attention"}
        and hermes.get("version")
        and "up to date" not in str(hermes.get("detail") or "").lower()
    ):
        rows.append({
            "id": "hermes-watch",
            "status": "attention" if hermes.get("status") == "attention" else "upgrade",
            "title": "Hermes update watch",
            "detail": hermes.get("detail") or "Hermes update check needs review.",
            "owner": "JAIMES",
        })
    npm_tags = (sources.get("openclawNpm") or {}).get("distTags") or {}
    beta = npm_tags.get("beta")
    if beta:
        rows.append({
            "id": "openclaw-beta-channel",
            "status": "watch",
            "title": "OpenCLAW beta channel tracked",
            "detail": f"npm beta tag: {beta}",
            "owner": "JOSHeX",
        })
    return rows


def publish(payload: dict[str, Any], agent: str) -> None:
    script = ROOT / "scripts" / "agent_publish.py"
    if not script.exists():
        return
    subprocess.run([
        sys.executable,
        str(script),
        "--agent", agent,
        "--type", "status",
        "--status", "done" if payload.get("status") == "ok" else "info",
        "--title", "Capability Watch refreshed",
        "--detail", payload.get("summary") or "Capability Watch refreshed.",
        "--tool", "capability-watch",
        "--brain-feed",
    ], cwd=ROOT, check=False, timeout=30)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh dashboard-safe capability watch metadata.")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--agent", default="jaimes")
    parser.add_argument("--no-network", action="store_true")
    args = parser.parse_args()

    now = utc_now()
    inventory = read_json(INVENTORY, {"nodes": []})
    sources: dict[str, Any] = {
        "installed": installed_summary(inventory),
        "openclawUpdate": openclaw_status(),
        "hermesUpdate": hermes_status(),
    }
    if not args.no_network:
        sources["openclawNpm"] = npm_dist_tags("openclaw")
        sources["openclawLatestRelease"] = fetch_json("https://api.github.com/repos/openclaw/openclaw/releases/latest")
        sources["hermesLatestRelease"] = fetch_json("https://api.github.com/repos/NousResearch/Hermes-Agent/releases/latest")
    recommendations = build_recommendations(sources)
    payload = {
        "updatedAt": now,
        "checkedAt": now,
        "status": "watch" if recommendations else "ok",
        "summary": f"{len(recommendations)} capability recommendation(s); {sources['installed']['openclawNodes']} OpenCLAW node(s), {sources['installed']['hermesNodes']} Hermes node(s).",
        "sources": sources,
        "recommendations": recommendations[:12],
        "privacy": "dashboard-safe metadata only",
    }
    write_json(OUT, payload)
    if args.publish:
        publish(payload, args.agent)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
