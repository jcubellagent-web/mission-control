#!/usr/bin/env python3
"""Export and ingest a privacy-safe CodexBar Ollama quota projection.

The exporter deliberately invokes CodexBar's supported CLI and rebuilds a new
document from an allowlist. Raw provider output, identity fields, cookies, and
tokens are never sent to Control Tower. SSH supplies transport authentication;
the ingest side enforces schema, freshness, monotonic observations, and atomic
mode-0600 storage.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional


SCHEMA_VERSION = 1
PROVIDER = "ollama"
MAX_DOCUMENT_BYTES = 16 * 1024
MAX_SOURCE_AGE = dt.timedelta(minutes=30)
MAX_FUTURE_SKEW = dt.timedelta(minutes=2)
TOP_LEVEL_FIELDS = frozenset({"schemaVersion", "provider", "observedAt", "exportedAt", "windows"})
WINDOW_FIELDS = frozenset({"id", "label", "usedPercent", "remainingPercent", "resetsAt", "windowMinutes"})
WINDOW_SPECS = (
    ("primary", "ollama-primary", "Session"),
    ("secondary", "ollama-secondary", "Weekly"),
)
DEFAULT_CODEXBAR_CLI = "/Applications/CodexBar.app/Contents/Helpers/CodexBarCLI"
DEFAULT_REMOTE = "josh2"
DEFAULT_REMOTE_SCRIPT = "/Users/josh2.0/.openclaw/workspace/mission-control/scripts/codexbar_quota_bridge.py"
DEFAULT_REMOTE_PATH = "/Users/josh2.0/.openclaw/workspace/mission-control/data/codexbar-quota-ollama.json"
DEFAULT_LOCK_PATH = "/tmp/com.joshex.codexbar-ollama-quota.lock"


class BridgeError(ValueError):
    """A bounded, safe-to-log bridge failure."""


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any, field: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(f"invalid-{field}")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BridgeError(f"invalid-{field}") from exc
    if parsed.tzinfo is None:
        raise BridgeError(f"invalid-{field}")
    return parsed.astimezone(dt.timezone.utc)


def bounded_percent(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise BridgeError(f"invalid-{field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BridgeError(f"invalid-{field}") from exc
    if not 0.0 <= number <= 100.0:
        raise BridgeError(f"invalid-{field}")
    return round(number, 2)


def build_projection(raw: Any, *, now: Optional[dt.datetime] = None) -> dict[str, Any]:
    """Reduce raw CodexBar JSON to the only fields allowed off this Mac."""
    current = (now or utc_now()).astimezone(dt.timezone.utc)
    entries = raw if isinstance(raw, list) else [raw]
    entry = next(
        (row for row in entries if isinstance(row, dict) and str(row.get("provider") or "").lower() == PROVIDER),
        None,
    )
    if not isinstance(entry, dict) or isinstance(entry.get("error"), dict):
        raise BridgeError("codexbar-provider-unavailable")
    usage = entry.get("usage")
    if not isinstance(usage, dict):
        raise BridgeError("codexbar-usage-missing")
    observed = parse_timestamp(usage.get("updatedAt"), "observedAt")
    if observed - current > MAX_FUTURE_SKEW:
        raise BridgeError("codexbar-observation-in-future")
    if current - observed > MAX_SOURCE_AGE:
        raise BridgeError("codexbar-observation-stale")

    windows: list[dict[str, Any]] = []
    for source_key, window_id, label in WINDOW_SPECS:
        source = usage.get(source_key)
        if not isinstance(source, dict):
            raise BridgeError(f"codexbar-{source_key}-missing")
        used = bounded_percent(source.get("usedPercent"), f"{source_key}-usedPercent")
        window_minutes = source.get("windowMinutes")
        if isinstance(window_minutes, bool) or not isinstance(window_minutes, int) or not 1 <= window_minutes <= 20160:
            raise BridgeError(f"invalid-{source_key}-windowMinutes")
        resets_at = parse_timestamp(source.get("resetsAt"), f"{source_key}-resetsAt")
        windows.append({
            "id": window_id,
            "label": label,
            "usedPercent": used,
            "remainingPercent": round(100.0 - used, 2),
            "resetsAt": iso_utc(resets_at),
            "windowMinutes": window_minutes,
        })
    return {
        "schemaVersion": SCHEMA_VERSION,
        "provider": PROVIDER,
        "observedAt": iso_utc(observed),
        "exportedAt": iso_utc(current),
        "windows": windows,
    }


def validate_projection(payload: Any, *, now: Optional[dt.datetime] = None) -> dict[str, Any]:
    """Validate the complete wire contract and return a normalized copy."""
    if not isinstance(payload, dict) or set(payload) != TOP_LEVEL_FIELDS:
        raise BridgeError("invalid-top-level-schema")
    if payload.get("schemaVersion") != SCHEMA_VERSION or payload.get("provider") != PROVIDER:
        raise BridgeError("invalid-provider-or-version")
    current = (now or utc_now()).astimezone(dt.timezone.utc)
    observed = parse_timestamp(payload.get("observedAt"), "observedAt")
    exported = parse_timestamp(payload.get("exportedAt"), "exportedAt")
    if observed - current > MAX_FUTURE_SKEW or exported - current > MAX_FUTURE_SKEW:
        raise BridgeError("projection-in-future")
    if current - observed > MAX_SOURCE_AGE:
        raise BridgeError("projection-stale")
    if exported + MAX_FUTURE_SKEW < observed:
        raise BridgeError("projection-export-before-observation")

    raw_windows = payload.get("windows")
    if not isinstance(raw_windows, list) or len(raw_windows) != len(WINDOW_SPECS):
        raise BridgeError("invalid-windows")
    expected = {(window_id, label) for _, window_id, label in WINDOW_SPECS}
    windows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_window in raw_windows:
        if not isinstance(raw_window, dict) or set(raw_window) != WINDOW_FIELDS:
            raise BridgeError("invalid-window-schema")
        key = (str(raw_window.get("id") or ""), str(raw_window.get("label") or ""))
        if key not in expected or key in seen:
            raise BridgeError("invalid-window-identity")
        seen.add(key)
        used = bounded_percent(raw_window.get("usedPercent"), "usedPercent")
        remaining = bounded_percent(raw_window.get("remainingPercent"), "remainingPercent")
        if abs((used + remaining) - 100.0) > 0.02:
            raise BridgeError("inconsistent-percentages")
        window_minutes = raw_window.get("windowMinutes")
        if isinstance(window_minutes, bool) or not isinstance(window_minutes, int) or not 1 <= window_minutes <= 20160:
            raise BridgeError("invalid-windowMinutes")
        resets_at = parse_timestamp(raw_window.get("resetsAt"), "resetsAt")
        windows.append({
            "id": key[0],
            "label": key[1],
            "usedPercent": used,
            "remainingPercent": remaining,
            "resetsAt": iso_utc(resets_at),
            "windowMinutes": window_minutes,
        })
    windows.sort(key=lambda row: 0 if row["id"] == "ollama-primary" else 1)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "provider": PROVIDER,
        "observedAt": iso_utc(observed),
        "exportedAt": iso_utc(exported),
        "windows": windows,
    }


def atomic_write_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise BridgeError("sidecar-path-is-symlink")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def ingest_projection(payload: Any, path: Path, *, now: Optional[dt.datetime] = None) -> str:
    current = (now or utc_now()).astimezone(dt.timezone.utc)
    normalized = validate_projection(payload, now=current)
    if path.exists():
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
            raise BridgeError("sidecar-path-invalid")
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            previous_observed = parse_timestamp(existing.get("observedAt"), "existing-observedAt")
        except (OSError, json.JSONDecodeError, BridgeError) as exc:
            raise BridgeError("existing-sidecar-invalid") from exc
        observed = parse_timestamp(normalized["observedAt"], "observedAt")
        if observed < previous_observed:
            raise BridgeError("replayed-observation")
        if observed == previous_observed:
            previous_core = {key: existing.get(key) for key in ("schemaVersion", "provider", "observedAt", "windows")}
            incoming_core = {key: normalized.get(key) for key in ("schemaVersion", "provider", "observedAt", "windows")}
            if previous_core != incoming_core:
                raise BridgeError("conflicting-duplicate-observation")
            return "duplicate"
    stored = dict(normalized)
    stored["receivedAt"] = iso_utc(current)
    atomic_write_private(path, stored)
    return "accepted"


def read_stdin_document() -> Any:
    raw = sys.stdin.buffer.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise BridgeError("projection-too-large")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("projection-json-invalid") from exc


def acquire_lock(path: Path):
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    handle = os.fdopen(descriptor, "r+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise BridgeError("export-already-running")
    return handle


def export_projection(args: argparse.Namespace) -> str:
    with acquire_lock(Path(args.lock_path)):
        try:
            proc = subprocess.run(
                [args.codexbar_cli, "usage", "--provider", PROVIDER, "--format", "json"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeError("codexbar-invocation-failed") from exc
        if proc.returncode != 0 or not proc.stdout.strip():
            raise BridgeError("codexbar-provider-unavailable")
        try:
            raw = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise BridgeError("codexbar-json-invalid") from exc
        projection = validate_projection(build_projection(raw))
        wire = json.dumps(projection, separators=(",", ":"), ensure_ascii=True)
        if args.stdout:
            print(wire)
            return "printed"
        command = [
            "/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            args.remote, "python3", args.remote_script, "ingest", "--path", args.remote_path, "--refresh",
        ]
        try:
            sent = subprocess.run(
                command,
                input=wire,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=45,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeError("bridge-send-failed") from exc
        if sent.returncode != 0:
            raise BridgeError("bridge-ingest-rejected")
        return "sent"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Project local CodexBar quota and send it over authenticated SSH.")
    export.add_argument("--codexbar-cli", default=DEFAULT_CODEXBAR_CLI)
    export.add_argument("--remote", default=DEFAULT_REMOTE)
    export.add_argument("--remote-script", default=DEFAULT_REMOTE_SCRIPT)
    export.add_argument("--remote-path", default=DEFAULT_REMOTE_PATH)
    export.add_argument("--lock-path", default=DEFAULT_LOCK_PATH)
    export.add_argument("--stdout", action="store_true", help="Print only the sanitized projection and do not send it.")
    ingest = commands.add_parser("ingest", help="Validate stdin and atomically promote the private runtime sidecar.")
    ingest.add_argument("--path", default=DEFAULT_REMOTE_PATH)
    ingest.add_argument("--refresh", action="store_true", help="Refresh Control Tower after a newly accepted observation.")
    return root


def main(argv: Optional[list[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "export":
            status = export_projection(args)
        else:
            status = ingest_projection(read_stdin_document(), Path(args.path))
            if args.refresh and status == "accepted":
                refresh_script = Path(__file__).resolve().with_name("update_mission_control.py")
                try:
                    refreshed = subprocess.run(
                        [sys.executable, str(refresh_script)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=40,
                        check=False,
                    )
                    status = "accepted-refreshed" if refreshed.returncode == 0 else "accepted-refresh-deferred"
                except (OSError, subprocess.TimeoutExpired):
                    status = "accepted-refresh-deferred"
        print(json.dumps({"ok": True, "status": status, "provider": PROVIDER}, separators=(",", ":")))
        return 0
    except BridgeError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "provider": PROVIDER}, separators=(",", ":")), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
