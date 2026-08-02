#!/usr/bin/env python3
"""Compare installed shared skills with canonical checked-in skill contracts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "agent-skills"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(canonical_root: Path, installed_root: Path) -> dict:
    rows = []
    for source in sorted(canonical_root.glob("*/SKILL.md")):
        target = installed_root / source.parent.name / "SKILL.md"
        status = "missing" if not target.exists() else "current" if digest(source) == digest(target) else "drifted"
        rows.append({"skill": source.parent.name, "status": status})
    counts = {status: sum(row["status"] == status for row in rows) for status in ("current", "drifted", "missing")}
    return {"ok": not counts["drifted"] and not counts["missing"], "counts": counts, "skills": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-root", default=str(CANONICAL))
    parser.add_argument("--installed-root", default=str(Path.home() / ".codex" / "skills"))
    args = parser.parse_args()
    payload = compare(Path(args.canonical_root), Path(args.installed_root))
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
