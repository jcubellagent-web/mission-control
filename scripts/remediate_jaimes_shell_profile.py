#!/usr/bin/env python3
"""Remove shell-wide provider secrets and noisy Homebrew startup from JAIMES."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
from pathlib import Path


def remediate(path: Path, backup_dir: Path, dry_run: bool = False) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    removed_secret_exports = 0
    fixed_brew_lines = 0
    shellenv_present = any("brew shellenv" in line for line in lines)
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("export ANTHROPIC_API_KEY="):
            removed_secret_exports += 1
            continue
        if stripped == "/opt/homebrew/bin/brew":
            fixed_brew_lines += 1
            if not shellenv_present:
                output.append('eval "$(/opt/homebrew/bin/brew shellenv)"')
                shellenv_present = True
            continue
        output.append(line)
    changed = output != lines
    backup = None
    if changed and not dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"zprofile-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.backup"
        shutil.copy2(path, backup)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write("\n".join(output).rstrip() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, path.stat().st_mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {
        "changed": changed,
        "dryRun": dry_run,
        "removedShellWideSecretExports": removed_secret_exports,
        "fixedBareBrewInvocations": fixed_brew_lines,
        "backupCreated": bool(backup),
        "credentialValueReadIntoOutput": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=Path.home() / ".zprofile")
    parser.add_argument("--backup-dir", type=Path, default=Path.home() / ".openclaw" / "backups" / "shell-profile-remediation")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = remediate(args.path, args.backup_dir, args.dry_run)
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
