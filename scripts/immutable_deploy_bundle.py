#!/usr/bin/env python3
"""Create, verify, and materialize deployments from immutable Git commits."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


def git(repo: Path, args: list[str], *, binary: bool = False) -> bytes | str:
    proc = subprocess.run(args, cwd=repo, capture_output=True, text=not binary)
    if proc.returncode:
        error = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode(errors="replace")
        raise SystemExit(error.strip())
    return proc.stdout


def normalize(value: str) -> str:
    raw = value.strip().replace("\\", "/").strip("/")
    path = PurePosixPath(raw)
    if not raw or raw == "." or path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise SystemExit(f"Unsafe deployment selector: {value!r}")
    return path.as_posix()


def tree(repo: Path, revision: str) -> tuple[str, dict[str, dict[str, str]]]:
    commit = str(git(repo, ["git", "rev-parse", f"{revision}^{{commit}}"])).strip()
    raw = git(repo, ["git", "ls-tree", "-r", "-z", commit], binary=True)
    assert isinstance(raw, bytes)
    entries: dict[str, dict[str, str]] = {}
    for row in raw.split(b"\0"):
        if not row:
            continue
        header, encoded_path = row.split(b"\t", 1)
        mode, kind, object_id = header.decode().split()
        path = encoded_path.decode("utf-8")
        entries[path] = {"mode": mode, "type": kind, "object": object_id}
    return commit, entries


def select(entries: dict[str, dict[str, str]], selectors: list[str]) -> list[str]:
    chosen = sorted(path for path in entries if any(path == item or path.startswith(item + "/") for item in selectors))
    if not chosen:
        raise SystemExit("Deployment selectors matched no committed files.")
    unsupported = [path for path in chosen if entries[path]["type"] != "blob" or entries[path]["mode"] == "120000"]
    if unsupported:
        raise SystemExit(f"Deployment inputs cannot contain symlinks or submodules: {unsupported}")
    return chosen


def blob(repo: Path, commit: str, path: str) -> bytes:
    value = git(repo, ["git", "show", f"{commit}:{path}"], binary=True)
    assert isinstance(value, bytes)
    return value


def bundle_digest(commit: str, files: list[dict[str, str]]) -> str:
    canonical = json.dumps({"revision": commit, "files": files}, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def create(repo: Path, revision: str, includes: list[str], output: Path, repo_key: str) -> dict[str, Any]:
    repo = repo.resolve()
    selectors = sorted(set(normalize(item) for item in includes))
    commit, entries = tree(repo, revision)
    files = []
    for path in select(entries, selectors):
        files.append({
            "path": path,
            "mode": "0755" if entries[path]["mode"] == "100755" else "0644",
            "sha256": hashlib.sha256(blob(repo, commit, path)).hexdigest(),
        })
    manifest = {
        "schemaVersion": 1, "repoKey": repo_key, "revision": commit,
        "selectors": selectors, "files": files,
        "bundleSha256": bundle_digest(commit, files),
    }
    if output.exists():
        raise SystemExit(f"Manifest already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    os.chmod(temporary, 0o644)
    temporary.replace(output)
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        raise SystemExit(f"Invalid deployment manifest: {exc}") from exc
    if payload.get("schemaVersion") != 1:
        raise SystemExit("Unsupported deployment manifest schema.")
    return payload


def verify(repo: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    commit, entries = tree(repo.resolve(), str(manifest.get("revision") or ""))
    selectors = [normalize(item) for item in manifest.get("selectors", [])]
    expected_paths = select(entries, selectors)
    files = manifest.get("files")
    if not isinstance(files, list) or [row.get("path") for row in files] != expected_paths:
        raise SystemExit("Manifest file set does not exactly match its selectors at the recorded commit.")
    verified = []
    for row in files:
        path = normalize(str(row.get("path") or ""))
        expected_mode = "0755" if entries[path]["mode"] == "100755" else "0644"
        digest = hashlib.sha256(blob(repo, commit, path)).hexdigest()
        if row.get("mode") != expected_mode or row.get("sha256") != digest:
            raise SystemExit(f"Immutable deployment input mismatch: {path}")
        verified.append({"path": path, "mode": expected_mode, "sha256": digest})
    if manifest.get("bundleSha256") != bundle_digest(commit, verified):
        raise SystemExit("Deployment bundle digest mismatch.")
    return {"ok": True, "revision": commit, "bundleSha256": manifest["bundleSha256"], "fileCount": len(verified)}


def materialize(repo: Path, manifest_path: Path, destination: Path) -> dict[str, Any]:
    result = verify(repo, manifest_path)
    manifest = load_manifest(manifest_path)
    destination = destination.resolve()
    if destination.exists():
        raise SystemExit(f"Deployment destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        for row in manifest["files"]:
            target = temporary / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob(repo.resolve(), manifest["revision"], row["path"]))
            os.chmod(target, int(row["mode"], 8))
        temporary.replace(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return result | {"destination": str(destination)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create_cmd = sub.add_parser("create")
    create_cmd.add_argument("--repo", required=True, type=Path)
    create_cmd.add_argument("--repo-key", required=True)
    create_cmd.add_argument("--revision", required=True)
    create_cmd.add_argument("--include", action="append", required=True)
    create_cmd.add_argument("--output", required=True, type=Path)
    for name in ("verify", "materialize"):
        command = sub.add_parser(name)
        command.add_argument("--repo", required=True, type=Path)
        command.add_argument("--manifest", required=True, type=Path)
        if name == "materialize":
            command.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "create":
        result = create(args.repo, args.revision, args.include, args.output, args.repo_key)
    elif args.command == "verify":
        result = verify(args.repo, args.manifest)
    else:
        result = materialize(args.repo, args.manifest, args.destination)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
