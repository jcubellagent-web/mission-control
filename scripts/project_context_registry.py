#!/usr/bin/env python3
"""Generate dashboard-safe project and chat context declarations."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "codex-project-contexts.json"
DATA = ROOT / "data"
OUT = DATA / "project-context-registry.json"
CHAT_OUT = DATA / "agent-chat-sources.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build(config_path: Path = CONFIG) -> tuple[dict[str, Any], dict[str, Any]]:
    config = read_json(config_path, {})
    projects = [row for row in config.get("projects", []) if isinstance(row, dict)]
    generated = utc_now()
    normalized = [{
        "key": str(row.get("key") or "")[:80],
        "label": str(row.get("label") or "")[:120],
        "scope": str(row.get("scope") or "")[:240],
        "contractVersion": int(row.get("contractVersion") or 0),
        "requiredRootFiles": ["AGENTS.md", "PROJECT_CONTEXT.md"],
        "memoryPreflight": "required",
        "artifactCloseout": "required",
    } for row in projects]
    covered = sum(row["contractVersion"] >= 1 for row in normalized)
    registry = {
        "schemaVersion": 1,
        "generatedAt": generated,
        "privacy": "Dashboard-safe declarations only; no project paths or private contents.",
        "canonicalMemory": config.get("canonicalMemory"),
        "sharedSources": config.get("sharedSources", []),
        "summary": {
            "projects": len(normalized),
            "covered": covered,
            "missing": len(normalized) - covered,
            "coveragePct": round(covered / len(normalized) * 100, 1) if normalized else None,
            "status": "ready" if normalized and covered == len(normalized) else "attention",
        },
        "projects": normalized,
    }
    chat_sources = {
        "schemaVersion": 1,
        "generatedAt": generated,
        "sources": [
            {"id": "governed-memory", "label": "Governed shared memory", "kind": "memory-registry", "required": True},
            {"id": "project-context", "label": "Codex project context contracts", "kind": "project-registry", "required": True},
            {"id": "control-tower", "label": "Control Tower operational state", "kind": "live-sidecars", "required": True}
        ],
    }
    return registry, chat_sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--output", default=str(OUT))
    parser.add_argument("--chat-output", default=str(CHAT_OUT))
    args = parser.parse_args()
    registry, chat_sources = build(Path(args.config))
    write_json(Path(args.output), registry)
    write_json(Path(args.chat_output), chat_sources)
    print(json.dumps({"ok": True, "summary": registry["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
