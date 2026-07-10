#!/usr/bin/env python3
"""Fail when Control Tower drifts from the canonical React/Vite source path."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    ROOT / "v2-react" / "index.html",
    ROOT / "v2-react" / "src" / "main.tsx",
    ROOT / "v2-react" / "src" / "styles.css",
    ROOT / "vite.config.ts",
    ROOT / "package.json",
)


def changed_paths() -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return [line[3:].strip() for line in proc.stdout.splitlines() if len(line) > 3]


def main() -> int:
    issues: list[str] = []
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED if not path.exists()]
    if missing:
        issues.append(f"missing canonical files: {', '.join(missing)}")

    package = json.loads((ROOT / "package.json").read_text())
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    active_scripts = " ".join(str(value) for value in scripts.values())
    if "3030" in active_scripts:
        issues.append("package scripts still reference retired port 3030")
    if "5174" not in str(scripts.get("dev", "")):
        issues.append("dev server is not pinned to canonical port 5174")

    vite_text = (ROOT / "vite.config.ts").read_text()
    if 'root: "v2-react"' not in vite_text:
        issues.append("Vite root is not v2-react")
    if 'outDir: "../dist/v2-react"' not in vite_text:
        issues.append("Vite output is not dist/v2-react")

    forbidden_changes = []
    for path in changed_paths():
        normalized = path.replace("\\", "/")
        if normalized == "index.html" or normalized.startswith("v2/") or normalized.startswith("dist/"):
            forbidden_changes.append(normalized)
    if forbidden_changes:
        issues.append(
            "legacy/generated paths are modified; use v2-react only: " + ", ".join(forbidden_changes)
        )

    if issues:
        print(json.dumps({"ok": False, "issues": issues}, indent=2))
        return 2
    print(json.dumps({
        "ok": True,
        "source": "v2-react",
        "port": 5174,
        "buildOutput": "dist/v2-react",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
