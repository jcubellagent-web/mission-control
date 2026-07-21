#!/usr/bin/env python3
"""Offline-first release gates for the versioned Telegram lifecycle.

The tool never talks to Telegram and never restarts a service.  Inventory and
parity probes are read-only.  Backup/install helpers are dry-run by default and
require an explicit confirmation string before they can change files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote


SCHEMA_VERSION = 2
ROLLOUT_SEQUENCE = ("off", "shadow", "josh2", "jaimes", "all")
REQUIRED_HOSTS = ("josh2", "jaimes")
ACTIVE_LIFECYCLE_VERSION = 3
REQUIRED_READER_VERSIONS = frozenset({2, 3})
REQUIRED_ARTIFACT_SCOPES: dict[str, str] = {
    "telegram-lifecycle": "shared",
    "telegram-rollout-config": "shared",
    "telegram-intake-lanes-config": "shared",
    "josh-fast-ack": "josh2",
    "jaimes-fast-ack": "jaimes",
    "josh-callback-action": "josh2",
    "josh-work-card": "josh2",
    "jaimes-work-card": "jaimes",
    "inbox-coordinator": "josh2",
    "jaimes-runtime-owner": "jaimes",
    "jaimes-runtime-owner-config": "jaimes",
    "brain-media-intake": "josh2",
    "brain-intake-worker": "josh2",
    "brain-gateway-dispatcher": "josh2",
    "brain-gateway-actions": "josh2",
    "brain-topic-manager": "josh2",
    "brain-actions-launchd": "josh2",
    "brain-dispatcher-launchd": "josh2",
    "brain-worker-launchd": "josh2",
    "openclaw-ingress": "josh2",
}
REQUIRED_ROLLBACK_STEPS = (
    "global-kill-switch-on",
    "stop-new-versioned-writes",
    "restore-deployed-entrypoints",
    "pin-new-work-to-n-minus-one",
    "drain-versioned-receipts",
    "verify-health",
)
SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
SAFE_REMOTE_TARGET_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
SAFE_REMOTE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")
VERSION_PATTERNS = (
    re.compile(r"\bLIFECYCLE_VERSION\s*=\s*[\"']?([0-9]+(?:\.[0-9]+)*)"),
    re.compile(r"\b__version__\s*=\s*[\"']([0-9]+(?:\.[0-9]+)*)[\"']"),
    re.compile(r"(?m)^version:\s*[\"']?([0-9]+(?:\.[0-9]+)*)[\"']?\s*$"),
)


class ReleaseError(RuntimeError):
    """A dashboard-safe release-gate failure."""


def utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def load_json_object(path: Path | str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError("invalid-json") from exc
    if not isinstance(value, dict):
        raise ReleaseError("invalid-json-shape")
    return value


def parse_cutover(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseError("invalid-cutover-time") from exc
    if parsed.tzinfo is None:
        raise ReleaseError("cutover-time-must-have-timezone")
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def extract_version(data: bytes) -> str:
    text = data[:512 * 1024].decode("utf-8", errors="replace")
    for pattern in VERSION_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""


def fingerprint_local(path: Path | str) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    try:
        info = candidate.lstat()
    except OSError:
        return {"ok": False, "error": "entrypoint-missing"}
    if not stat.S_ISREG(info.st_mode) or candidate.is_symlink():
        return {"ok": False, "error": "entrypoint-not-regular"}
    data = candidate.read_bytes()
    return {
        "ok": True,
        "sha256": hashlib.sha256(data).hexdigest(),
        "version": extract_version(data),
        "bytes": len(data),
        "executable": bool(info.st_mode & stat.S_IXUSR),
    }


REMOTE_FINGERPRINT_PROGRAM = r'''import hashlib,json,os,re,stat,sys
p=sys.argv[1]
try:
 s=os.lstat(p)
 if not stat.S_ISREG(s.st_mode) or stat.S_ISLNK(s.st_mode): raise OSError("not-regular")
 d=open(p,"rb").read()
 t=d[:524288].decode("utf-8","replace")
 patterns=(r"\bLIFECYCLE_VERSION\s*=\s*[\"']?([0-9]+(?:\.[0-9]+)*)",r"\b__version__\s*=\s*[\"']([0-9]+(?:\.[0-9]+)*)[\"']",r"(?m)^version:\s*[\"']?([0-9]+(?:\.[0-9]+)*)[\"']?\s*$")
 v=""
 for x in patterns:
  m=re.search(x,t)
  if m: v=m.group(1); break
 print(json.dumps({"ok":True,"sha256":hashlib.sha256(d).hexdigest(),"version":v,"bytes":len(d),"executable":bool(s.st_mode&stat.S_IXUSR)}))
except Exception:
 print(json.dumps({"ok":False,"error":"entrypoint-unreadable"}))
'''


REMOTE_INVENTORY_PROGRAM = r'''import json,sqlite3,sys,urllib.parse
p=sys.argv[1]; writer=int(sys.argv[2]); cutoff=None if sys.argv[3]=="-" else sys.argv[3]; owner=sys.argv[4]
out={"ok":False,"error":"inventory-unreadable"}
try:
 db=sqlite3.connect("file:"+urllib.parse.quote(p)+"?mode=ro",uri=True)
 db.row_factory=sqlite3.Row; db.execute("PRAGMA query_only=ON")
 tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 need={"work_receipts","effects","terminal_outbox","shadow_samples"}
 if not need.issubset(tables): raise RuntimeError("schema")
 columns={r[1] for r in db.execute("PRAGMA table_info(work_receipts)")}
 if not {"shadow_only","current_owner"}.issubset(columns): raise RuntimeError("schema")
 shadow_columns={r[1] for r in db.execute("PRAGMA table_info(shadow_samples)")}
 if not {"terminal_observed","terminal_delivered"}.issubset(shadow_columns): raise RuntimeError("schema")
 where="created_at < ?" if cutoff else "1=1"; params=(cutoff,) if cutoff else ()
 total=db.execute("SELECT COUNT(*) FROM work_receipts").fetchone()[0]
 selected=db.execute("SELECT COUNT(*) FROM work_receipts WHERE "+where,params).fetchone()[0]
 opened=db.execute("SELECT COUNT(*) FROM work_receipts WHERE "+where+" AND shadow_only=0 AND (phase!='terminal' OR delivery_state!='delivered')",params).fetchone()[0]
 ind=db.execute("SELECT COUNT(*) FROM work_receipts WHERE "+where+" AND shadow_only=0 AND delivery_state='indeterminate'",params).fetchone()[0]
 n1=db.execute("SELECT COUNT(*) FROM work_receipts WHERE "+where+" AND lifecycle_version=?",params+(writer-1,)).fetchone()[0]
 unsupported=db.execute("SELECT COUNT(*) FROM work_receipts WHERE "+where+" AND lifecycle_version NOT IN (?,?)",params+(writer-1,writer)).fetchone()[0]
 visible_where=("w.created_at < ?" if cutoff else "1=1")+" AND w.shadow_only=0"
 effects=db.execute("SELECT COUNT(*) FROM effects e JOIN work_receipts w ON w.work_id=e.work_id WHERE "+visible_where+" AND e.state!='delivered'",params).fetchone()[0]
 outbox=db.execute("SELECT COUNT(*) FROM terminal_outbox o JOIN work_receipts w ON w.work_id=o.work_id WHERE "+visible_where+" AND o.state!='delivered'",params).fetchone()[0]
 shadow_total=db.execute("SELECT COUNT(*) FROM work_receipts WHERE shadow_only=1").fetchone()[0]
 shadow_open=db.execute("SELECT COUNT(*) FROM work_receipts WHERE shadow_only=1 AND phase!='terminal'").fetchone()[0]
 shadow_pending=db.execute("SELECT COUNT(*) FROM terminal_outbox o JOIN work_receipts w ON w.work_id=o.work_id WHERE w.shadow_only=1 AND o.state!='delivered'").fetchone()[0]
 shadow_samples=db.execute("SELECT COUNT(*),COALESCE(SUM(matched=1 AND terminal_observed=1 AND terminal_delivered=1),0),COALESCE(SUM(terminal_observed=1),0) FROM shadow_samples WHERE owner=?",(owner,)).fetchone()
 shadow_dirty=int(shadow_samples[2])-int(shadow_samples[1]); shadow_unobserved=int(shadow_samples[0])-int(shadow_samples[2])
 shadow_unsampled=db.execute("SELECT COUNT(*) FROM work_receipts w LEFT JOIN shadow_samples s ON s.work_id=w.work_id WHERE w.shadow_only=1 AND s.work_id IS NULL").fetchone()[0]
 shadow_mismatch=db.execute("SELECT COUNT(*) FROM work_receipts WHERE shadow_only=1 AND current_owner!=?",(owner,)).fetchone()[0]+db.execute("SELECT COUNT(*) FROM shadow_samples WHERE owner!=?",(owner,)).fetchone()[0]
 out={"ok":True,"totalReceipts":int(total),"preCutoverReceipts":int(selected),"openReceipts":int(opened),"indeterminateReceipts":int(ind),"nMinusOneReceipts":int(n1),"unsupportedReceipts":int(unsupported),"openEffects":int(effects),"openTerminalOutbox":int(outbox),"shadowReceipts":int(shadow_total),"openShadowReceipts":int(shadow_open),"shadowPendingOutbox":int(shadow_pending),"shadowSamples":int(shadow_samples[0]),"cleanShadowSamples":int(shadow_samples[1]),"dirtyShadowSamples":int(shadow_dirty),"unobservedShadowSamples":int(shadow_unobserved),"unsampledShadowReceipts":int(shadow_unsampled),"shadowOwnerMismatches":int(shadow_mismatch)}
except Exception:
 pass
print(json.dumps(out))
'''


def _remote_json(
    host: Mapping[str, Any],
    program: str,
    args: Sequence[str],
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    target = str(host.get("target") or "")
    python = str(host.get("python") or "/usr/bin/python3")
    if not SAFE_REMOTE_TARGET_RE.fullmatch(target):
        return {"ok": False, "error": "invalid-ssh-target"}
    if not SAFE_REMOTE_PATH_RE.fullmatch(python):
        return {"ok": False, "error": "invalid-remote-python"}
    if any(value and not SAFE_REMOTE_PATH_RE.fullmatch(value) and not value.isdigit() for value in args):
        # ISO cutover timestamps are the sole non-path/non-numeric argument.
        for value in args:
            if value and not (
                SAFE_REMOTE_PATH_RE.fullmatch(value)
                or value.isdigit()
                or re.fullmatch(r"[0-9T:+.Z-]+", value)
                or SAFE_NAME_RE.fullmatch(value)
            ):
                return {"ok": False, "error": "invalid-remote-argument"}
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", target, python, "-", *args],
            input=program,
            text=True,
            capture_output=True,
            timeout=max(5, min(int(timeout), 120)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "error": "remote-probe-failed"}
    if proc.returncode != 0:
        return {"ok": False, "error": "remote-probe-failed"}
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "remote-probe-invalid"}
    return value if isinstance(value, dict) else {"ok": False, "error": "remote-probe-invalid"}


def fingerprint_host(host: Mapping[str, Any], path: str) -> dict[str, Any]:
    transport = str(host.get("transport") or "local")
    if transport == "local":
        return fingerprint_local(path)
    if transport == "ssh":
        if not SAFE_REMOTE_PATH_RE.fullmatch(path):
            return {"ok": False, "error": "invalid-remote-entrypoint"}
        return _remote_json(host, REMOTE_FINGERPRINT_PROGRAM, [path])
    return {"ok": False, "error": "unknown-host-transport"}


def inventory_local(
    database: Path | str,
    *,
    writer_version: int,
    cutover_at: str | None = None,
    expected_owner: str = "josh2",
) -> dict[str, Any]:
    if expected_owner not in REQUIRED_HOSTS:
        return {"ok": False, "error": "inventory-owner-invalid"}
    path = Path(database).expanduser()
    if not path.is_file() or path.is_symlink():
        return {"ok": False, "error": "inventory-database-missing"}
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        tables = {
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"work_receipts", "effects", "terminal_outbox", "shadow_samples"}.issubset(tables):
            raise ReleaseError("inventory-schema-missing")
        work_columns = {
            str(row[1]) for row in db.execute("PRAGMA table_info(work_receipts)")
        }
        if not {"shadow_only", "current_owner"}.issubset(work_columns):
            raise ReleaseError("inventory-schema-missing")
        shadow_columns = {
            str(row[1]) for row in db.execute("PRAGMA table_info(shadow_samples)")
        }
        if not {"terminal_observed", "terminal_delivered"}.issubset(shadow_columns):
            raise ReleaseError("inventory-schema-missing")
        where = "created_at < ?" if cutover_at else "1=1"
        params: tuple[Any, ...] = (cutover_at,) if cutover_at else ()
        total = int(db.execute("SELECT COUNT(*) FROM work_receipts").fetchone()[0])
        selected = int(
            db.execute(f"SELECT COUNT(*) FROM work_receipts WHERE {where}", params).fetchone()[0]
        )
        opened = int(
            db.execute(
                f"SELECT COUNT(*) FROM work_receipts WHERE {where} "
                "AND shadow_only=0 "
                "AND (phase!='terminal' OR delivery_state!='delivered')",
                params,
            ).fetchone()[0]
        )
        indeterminate = int(
            db.execute(
                f"SELECT COUNT(*) FROM work_receipts WHERE {where} "
                "AND shadow_only=0 AND delivery_state='indeterminate'",
                params,
            ).fetchone()[0]
        )
        n_minus_one = int(
            db.execute(
                f"SELECT COUNT(*) FROM work_receipts WHERE {where} AND lifecycle_version=?",
                params + (int(writer_version) - 1,),
            ).fetchone()[0]
        )
        unsupported = int(
            db.execute(
                f"SELECT COUNT(*) FROM work_receipts WHERE {where} "
                "AND lifecycle_version NOT IN (?,?)",
                params + (int(writer_version) - 1, int(writer_version)),
            ).fetchone()[0]
        )
        effect_where = ("w.created_at < ?" if cutover_at else "1=1") + " AND w.shadow_only=0"
        open_effects = int(
            db.execute(
                "SELECT COUNT(*) FROM effects e JOIN work_receipts w ON w.work_id=e.work_id "
                f"WHERE {effect_where} AND e.state!='delivered'",
                params,
            ).fetchone()[0]
        )
        open_outbox = int(
            db.execute(
                "SELECT COUNT(*) FROM terminal_outbox o "
                "JOIN work_receipts w ON w.work_id=o.work_id "
                f"WHERE {effect_where} AND o.state!='delivered'",
                params,
            ).fetchone()[0]
        )
        shadow_receipts = int(
            db.execute("SELECT COUNT(*) FROM work_receipts WHERE shadow_only=1").fetchone()[0]
        )
        open_shadow = int(
            db.execute(
                "SELECT COUNT(*) FROM work_receipts WHERE shadow_only=1 AND phase!='terminal'"
            ).fetchone()[0]
        )
        shadow_pending_outbox = int(
            db.execute(
                "SELECT COUNT(*) FROM terminal_outbox o "
                "JOIN work_receipts w ON w.work_id=o.work_id "
                "WHERE w.shadow_only=1 AND o.state!='delivered'"
            ).fetchone()[0]
        )
        shadow_row = db.execute(
            """SELECT COUNT(*) AS total,
                      COALESCE(SUM(
                        matched=1 AND terminal_observed=1 AND terminal_delivered=1
                      ),0) AS clean,
                      COALESCE(SUM(terminal_observed=1),0) AS observed
                 """
            "FROM shadow_samples WHERE owner=?",
            (expected_owner,),
        ).fetchone()
        shadow_samples = int(shadow_row["total"] or 0)
        clean_shadow_samples = int(shadow_row["clean"] or 0)
        observed_shadow_samples = int(shadow_row["observed"] or 0)
        unsampled_shadow = int(
            db.execute(
                "SELECT COUNT(*) FROM work_receipts w "
                "LEFT JOIN shadow_samples s ON s.work_id=w.work_id "
                "WHERE w.shadow_only=1 AND s.work_id IS NULL"
            ).fetchone()[0]
        )
        shadow_owner_mismatches = int(
            db.execute(
                "SELECT COUNT(*) FROM work_receipts "
                "WHERE shadow_only=1 AND current_owner!=?",
                (expected_owner,),
            ).fetchone()[0]
        ) + int(
            db.execute(
                "SELECT COUNT(*) FROM shadow_samples WHERE owner!=?",
                (expected_owner,),
            ).fetchone()[0]
        )
    except (sqlite3.Error, ReleaseError):
        return {"ok": False, "error": "inventory-unreadable"}
    finally:
        try:
            db.close()
        except UnboundLocalError:
            pass
    return {
        "ok": True,
        "totalReceipts": total,
        "preCutoverReceipts": selected,
        "openReceipts": opened,
        "indeterminateReceipts": indeterminate,
        "nMinusOneReceipts": n_minus_one,
        "unsupportedReceipts": unsupported,
        "openEffects": open_effects,
        "openTerminalOutbox": open_outbox,
        "shadowReceipts": shadow_receipts,
        "openShadowReceipts": open_shadow,
        # Shadow terminal outboxes are deliberately never delivered.  They are
        # reported for audit but excluded from the visible-writer drain gate.
        "shadowPendingOutbox": shadow_pending_outbox,
        "shadowSamples": shadow_samples,
        "cleanShadowSamples": clean_shadow_samples,
        "dirtyShadowSamples": observed_shadow_samples - clean_shadow_samples,
        "unobservedShadowSamples": shadow_samples - observed_shadow_samples,
        "unsampledShadowReceipts": unsampled_shadow,
        "shadowOwnerMismatches": shadow_owner_mismatches,
    }


def inventory_host(
    host: Mapping[str, Any],
    *,
    writer_version: int,
    cutover_at: str | None = None,
    expected_owner: str = "josh2",
) -> dict[str, Any]:
    database = str(host.get("lifecycleDb") or "")
    transport = str(host.get("transport") or "local")
    if transport == "local":
        return inventory_local(
            database,
            writer_version=writer_version,
            cutover_at=cutover_at,
            expected_owner=expected_owner,
        )
    if transport == "ssh":
        if not SAFE_REMOTE_PATH_RE.fullmatch(database):
            return {"ok": False, "error": "invalid-remote-inventory-path"}
        return _remote_json(
            host,
            REMOTE_INVENTORY_PROGRAM,
            [database, str(int(writer_version)), cutover_at or "-", expected_owner],
        )
    return {"ok": False, "error": "unknown-host-transport"}


def cross_host_inventory(
    manifest: Mapping[str, Any],
    *,
    writer_version: int,
    cutover_at: str | None = None,
) -> dict[str, Any]:
    hosts = manifest.get("hosts") if isinstance(manifest.get("hosts"), dict) else {}
    rows: dict[str, Any] = {}
    for name in REQUIRED_HOSTS:
        host = hosts.get(name)
        rows[name] = (
            inventory_host(
                host,
                writer_version=writer_version,
                cutover_at=cutover_at,
                expected_owner=name,
            )
            if isinstance(host, dict)
            else {"ok": False, "error": "required-host-missing"}
        )
    count_fields = (
        "openReceipts",
        "indeterminateReceipts",
        "unsupportedReceipts",
        "openEffects",
        "openTerminalOutbox",
    )
    drained = all(
        bool(row.get("ok")) and all(int(row.get(field) or 0) == 0 for field in count_fields)
        for row in rows.values()
    )
    return {
        "ok": drained,
        "status": "drained" if drained else "blocked",
        "hostCount": len(rows),
        "hosts": rows,
    }


def parity_matrix(manifest: Mapping[str, Any]) -> dict[str, Any]:
    artifacts = (
        manifest.get("sourceArtifacts")
        if isinstance(manifest.get("sourceArtifacts"), dict)
        else {}
    )
    hosts = manifest.get("hosts") if isinstance(manifest.get("hosts"), dict) else {}
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    source_fingerprints: dict[str, dict[str, Any]] = {}
    artifact_names = {str(name) for name in artifacts}
    missing = sorted(set(REQUIRED_ARTIFACT_SCOPES) - artifact_names)
    unexpected = sorted(artifact_names - set(REQUIRED_ARTIFACT_SCOPES))
    problems.extend(f"required-artifact-missing:{name}" for name in missing)
    problems.extend(f"unexpected-artifact:{name}" for name in unexpected)
    for artifact_name in REQUIRED_ARTIFACT_SCOPES:
        artifact = artifacts.get(artifact_name)
        if not isinstance(artifact, dict):
            continue
        declared_scope = str(artifact.get("scope") or "")
        expected_scope = REQUIRED_ARTIFACT_SCOPES[artifact_name]
        if declared_scope != expected_scope:
            problems.append(f"artifact-scope-mismatch:{artifact_name}")
        source = fingerprint_local(str(artifact.get("path") or ""))
        declared_version = str(artifact.get("version") or "")
        expected_sha256 = str(artifact.get("expectedSha256") or "")
        if expected_sha256 and not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            problems.append(f"artifact-expected-checksum-invalid:{artifact_name}")
        if not declared_version and not expected_sha256:
            problems.append(f"artifact-integrity-metadata-missing:{artifact_name}")
        embedded_version = str(source.get("version") or "")
        version_metadata_match = bool(
            declared_version
            and (not embedded_version or embedded_version == declared_version)
        )
        checksum_metadata_match = bool(
            expected_sha256
            and source.get("ok")
            and source.get("sha256") == expected_sha256
        )
        source["scope"] = declared_scope
        source["versionDeclared"] = declared_version
        source["expectedSha256"] = expected_sha256
        source["integrityMetadataMatch"] = bool(
            version_metadata_match or checksum_metadata_match
        )
        source_fingerprints[artifact_name] = source

    for artifact_name, source in source_fingerprints.items():
        scope = REQUIRED_ARTIFACT_SCOPES[artifact_name]
        target_hosts = REQUIRED_HOSTS if scope == "shared" else (scope,)
        for host_name in target_hosts:
            host = hosts.get(host_name)
            entrypoints = host.get("entrypoints") if isinstance(host, dict) and isinstance(host.get("entrypoints"), dict) else {}
            deployed_path = str(entrypoints.get(artifact_name) or "")
            deployed = (
                fingerprint_host(host, deployed_path)
                if isinstance(host, dict) and deployed_path
                else {"ok": False, "error": "deployed-entrypoint-missing"}
            )
            checksum_match = bool(
                source.get("ok")
                and deployed.get("ok")
                and source.get("sha256") == deployed.get("sha256")
            )
            version_match = bool(
                source.get("ok")
                and deployed.get("ok")
                and source.get("integrityMetadataMatch")
                and (
                    not source.get("versionDeclared")
                    or not deployed.get("version")
                    or source.get("versionDeclared") == deployed.get("version")
                )
            )
            rows.append(
                {
                    "host": host_name,
                    "entrypoint": artifact_name,
                    "scope": scope,
                    "ok": checksum_match and version_match,
                    "checksumMatch": checksum_match,
                    "versionMatch": version_match,
                    "sourceChecksum": source.get("sha256", ""),
                    "deployedChecksum": deployed.get("sha256", ""),
                    "sourceVersion": source.get("version", ""),
                    "deployedVersion": deployed.get("version", ""),
                    "error": source.get("error") or deployed.get("error") or "",
                }
            )
    expected_rows = sum(
        len(REQUIRED_HOSTS) if scope == "shared" else 1
        for scope in REQUIRED_ARTIFACT_SCOPES.values()
    )
    ok = (
        not problems
        and set(source_fingerprints) == set(REQUIRED_ARTIFACT_SCOPES)
        and len(rows) == expected_rows
        and all(row["ok"] for row in rows)
    )
    return {
        "ok": ok,
        "status": "matched" if ok else "mismatch",
        "artifactCount": len(source_fingerprints),
        "hostCount": len(REQUIRED_HOSTS),
        "expectedRowCount": expected_rows,
        "problems": sorted(problems),
        "rows": rows,
    }


def validate_transition(current: str, target: str) -> dict[str, Any]:
    current = str(current or "")
    target = str(target or "")
    if current not in ROLLOUT_SEQUENCE or target not in ROLLOUT_SEQUENCE:
        return {"ok": False, "status": "invalid-state"}
    expected = (
        ROLLOUT_SEQUENCE[ROLLOUT_SEQUENCE.index(current) + 1]
        if current != ROLLOUT_SEQUENCE[-1]
        else ""
    )
    ok = bool(expected and target == expected)
    return {
        "ok": ok,
        "status": "allowed" if ok else "rejected",
        "current": current,
        "target": target,
        "expected": expected,
    }


def validate_transition_history(history: Any, current: str) -> dict[str, Any]:
    if not isinstance(history, list) or not history:
        return {"ok": False, "status": "history-missing"}
    normalized = [str(value or "") for value in history]
    expected = list(ROLLOUT_SEQUENCE[: len(normalized)])
    ok = normalized == expected and normalized[-1] == current
    return {
        "ok": ok,
        "status": "valid" if ok else "invalid",
        "states": normalized,
    }


def validate_rollout_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    problems: list[str] = []
    state = str(policy.get("masterState") or "")
    if state not in ROLLOUT_SEQUENCE:
        problems.append("unknown-master-state")
    try:
        writer = int(policy.get("writerLifecycleVersion"))
    except (TypeError, ValueError):
        writer = 0
        problems.append("writer-version-invalid")
    try:
        readers = {int(value) for value in policy.get("readerLifecycleVersions") or []}
    except (TypeError, ValueError):
        readers = set()
    if writer not in REQUIRED_READER_VERSIONS or not REQUIRED_READER_VERSIONS.issubset(readers):
        problems.append("n-and-n-minus-one-readers-required")
    if not isinstance(policy.get("globalKillSwitch"), bool):
        problems.append("global-kill-switch-missing")
    if not isinstance(policy.get("brainKillSwitch"), bool):
        problems.append("brain-kill-switch-missing")
    hosts = policy.get("hosts")
    if not isinstance(hosts, dict) or any(not isinstance(hosts.get(name), bool) for name in REQUIRED_HOSTS):
        problems.append("host-kill-switches-missing")
    try:
        if int(policy.get("shadowMinimumPerOwner") or 0) < 20:
            problems.append("shadow-evidence-floor-too-low")
    except (TypeError, ValueError):
        problems.append("shadow-evidence-floor-invalid")
    try:
        if int(policy.get("brainFixtureMinimum") or 0) < 20:
            problems.append("brain-fixture-floor-too-low")
    except (TypeError, ValueError):
        problems.append("brain-fixture-floor-invalid")
    rollback = policy.get("rollback")
    if not isinstance(rollback, dict):
        problems.append("rollback-policy-missing")
    else:
        if not isinstance(rollback.get("newWorkToLegacy"), bool):
            problems.append("legacy-writer-switch-missing")
        if rollback.get("drainExistingVersionedWork") is not True:
            problems.append("versioned-drain-policy-required")
        if writer == ACTIVE_LIFECYCLE_VERSION - 1 and rollback.get("newWorkToLegacy") is not True:
            problems.append("legacy-writer-pin-required")
    if writer == ACTIVE_LIFECYCLE_VERSION - 1 and state != "off":
        problems.append("rollback-master-state-must-be-off")
    return {
        "ok": not problems,
        "status": "valid" if not problems else "invalid",
        "masterState": state,
        "writerVersion": writer,
        "activeLifecycleVersion": ACTIVE_LIFECYCLE_VERSION,
        "readerVersions": sorted(readers),
        "globalKillSwitch": policy.get("globalKillSwitch"),
        "brainKillSwitch": policy.get("brainKillSwitch"),
        "problems": sorted(set(problems)),
    }


def verify_rollback_plan(plan: Any, *, writer_version: int) -> dict[str, Any]:
    problems: list[str] = []
    if not isinstance(plan, dict):
        return {"ok": False, "status": "missing", "problems": ["rollback-plan-missing"]}
    if plan.get("restoreFromBackup") is not True:
        problems.append("backup-restore-not-confirmed")
    if plan.get("preserveVersionedDrain") is not True:
        problems.append("versioned-drain-not-preserved")
    try:
        if int(plan.get("nMinusOneVersion")) != int(writer_version) - 1:
            problems.append("n-minus-one-version-mismatch")
    except (TypeError, ValueError):
        problems.append("n-minus-one-version-missing")
    steps = plan.get("steps")
    if not isinstance(steps, list):
        problems.append("rollback-steps-missing")
    else:
        positions = {str(value): index for index, value in enumerate(steps)}
        if any(step not in positions for step in REQUIRED_ROLLBACK_STEPS):
            problems.append("rollback-step-missing")
        elif [positions[step] for step in REQUIRED_ROLLBACK_STEPS] != sorted(
            positions[step] for step in REQUIRED_ROLLBACK_STEPS
        ):
            problems.append("rollback-step-order-invalid")
    return {
        "ok": not problems,
        "status": "verified" if not problems else "invalid",
        "problems": sorted(set(problems)),
    }


def verify_brain_fixture_gate(
    rollout: Mapping[str, Any],
    attestation_path: Path | str | None,
) -> dict[str, Any]:
    """Require a private, locally verified fixture attestation before Brain-on.

    The private path is supplied at invocation time and is never read from or
    written into tracked rollout configuration.  Brain cannot run in ``off`` or
    ``shadow``, so those stages remain explicitly not-required.
    """
    try:
        minimum = max(20, int(rollout.get("brainFixtureMinimum") or 20))
    except (TypeError, ValueError):
        minimum = 20
    required = bool(
        rollout.get("brainKillSwitch") is False
        and rollout.get("globalKillSwitch") is False
        and str(rollout.get("masterState") or "") in {"josh2", "all"}
        and isinstance(rollout.get("hosts"), dict)
        and rollout.get("hosts", {}).get("josh2") is True
    )
    if not required:
        return {
            "ok": True,
            "status": "not-required",
            "required": False,
            "minimum": minimum,
        }
    if not attestation_path:
        return {
            "ok": False,
            "status": "blocked",
            "required": True,
            "minimum": minimum,
            "problems": ["brain-fixture-attestation-required"],
        }
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        suite = importlib.import_module("brain_fixture_suite")
        verified = suite.verify_attestation(Path(attestation_path))
    except Exception:
        verified = {
            "ok": False,
            "status": "blocked",
            "problems": ["brain-fixture-verifier-unavailable"],
        }
    flow_count = int(verified.get("flowCaseCount") or 0)
    problems = list(verified.get("problems") or [])
    if flow_count < minimum:
        problems.append("brain-fixture-minimum-not-met")
    ok = bool(verified.get("ok")) and not problems
    return {
        "ok": ok,
        "status": "verified" if ok else "blocked",
        "required": True,
        "minimum": minimum,
        "flowCaseCount": flow_count,
        "faultCaseCount": int(verified.get("faultCaseCount") or 0),
        "allClean": bool(verified.get("allClean")),
        "attestationDigest": str(verified.get("attestationDigest") or ""),
        "problems": sorted(set(problems)),
    }


def verify_shadow_evidence_gate(
    rollout: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    """Require real, terminal, clean shadow observations from both owners.

    Shadow receipts intentionally never deliver their private terminal outbox.
    Promotion is therefore based on the observed legacy-vs-v3 comparison and
    terminal shadow state, not a caller-asserted delivery flag.
    """
    try:
        minimum = max(20, int(rollout.get("shadowMinimumPerOwner") or 20))
    except (TypeError, ValueError):
        minimum = 20
    required = str(rollout.get("masterState") or "") in {"josh2", "jaimes", "all"}
    if not required:
        return {
            "ok": True,
            "status": "not-required",
            "required": False,
            "minimumPerOwner": minimum,
        }

    hosts = inventory.get("hosts") if isinstance(inventory.get("hosts"), dict) else {}
    rows: dict[str, Any] = {}
    problems: list[str] = []
    for owner in REQUIRED_HOSTS:
        source = hosts.get(owner) if isinstance(hosts.get(owner), dict) else {}
        total = int(source.get("shadowSamples") or 0)
        clean = int(source.get("cleanShadowSamples") or 0)
        dirty = int(source.get("dirtyShadowSamples") or 0)
        unobserved = int(source.get("unobservedShadowSamples") or 0)
        open_receipts = int(source.get("openShadowReceipts") or 0)
        unsampled = int(source.get("unsampledShadowReceipts") or 0)
        mismatches = int(source.get("shadowOwnerMismatches") or 0)
        owner_problems: list[str] = []
        if not source.get("ok"):
            owner_problems.append("inventory-unavailable")
        if total < minimum:
            owner_problems.append("minimum-not-met")
        if clean != total or dirty:
            owner_problems.append("unclean-sample")
        if unobserved:
            owner_problems.append("terminal-observation-missing")
        if open_receipts:
            owner_problems.append("shadow-receipt-open")
        if unsampled:
            owner_problems.append("shadow-receipt-unsampled")
        if mismatches:
            owner_problems.append("owner-mismatch")
        if owner_problems:
            problems.extend(f"{owner}:{problem}" for problem in owner_problems)
        rows[owner] = {
            "ok": not owner_problems,
            "total": total,
            "clean": clean,
            "dirty": dirty,
            "unobserved": unobserved,
            "openReceipts": open_receipts,
            "unsampledReceipts": unsampled,
            "ownerMismatches": mismatches,
        }
    return {
        "ok": not problems,
        "status": "verified" if not problems else "blocked",
        "required": True,
        "minimumPerOwner": minimum,
        "owners": rows,
        "problems": sorted(problems),
    }


def release_preflight(
    manifest: Mapping[str, Any],
    rollout: Mapping[str, Any],
    *,
    cutover_at: str | None = None,
    brain_attestation_path: Path | str | None = None,
) -> dict[str, Any]:
    policy = validate_rollout_policy(rollout)
    writer_version = int(policy.get("activeLifecycleVersion") or 0)
    history = validate_transition_history(
        manifest.get("rolloutHistory"),
        str(policy.get("masterState") or ""),
    )
    rollback = verify_rollback_plan(
        manifest.get("rollbackPlan"),
        writer_version=writer_version,
    )
    inventory = cross_host_inventory(
        manifest,
        writer_version=writer_version,
        cutover_at=parse_cutover(cutover_at),
    )
    parity = parity_matrix(manifest)
    checks = {
        "rolloutPolicy": policy,
        "rolloutHistory": history,
        "rollbackPlan": rollback,
        "inventoryDrain": inventory,
        "deployedParity": parity,
        "shadowEvidenceGate": verify_shadow_evidence_gate(rollout, inventory),
        "brainFixtureGate": verify_brain_fixture_gate(rollout, brain_attestation_path),
    }
    ok = all(bool(value.get("ok")) for value in checks.values())
    return {
        "schemaVersion": SCHEMA_VERSION,
        "ok": ok,
        "status": "ready" if ok else "blocked",
        "checkCount": len(checks),
        "passedCount": sum(bool(value.get("ok")) for value in checks.values()),
        "checks": checks,
    }


def _assert_safe_mutation_paths(source: Path, destination: Path, backup_dir: Path) -> None:
    source = source.expanduser().resolve(strict=False)
    destination = destination.expanduser().resolve(strict=False)
    backup_dir = backup_dir.expanduser().resolve(strict=False)
    broad = {Path("/"), Path.home().resolve()}
    if destination in broad or backup_dir in broad:
        raise ReleaseError("unsafe-broad-mutation-path")
    if source == destination or destination == backup_dir:
        raise ReleaseError("overlapping-mutation-path")


def backup_artifact(
    source: Path | str,
    backup_dir: Path | str,
    *,
    name: str,
    apply: bool = False,
    confirmation: str = "",
) -> dict[str, Any]:
    if not SAFE_NAME_RE.fullmatch(name):
        raise ReleaseError("invalid-artifact-name")
    source_path = Path(source).expanduser()
    backup_root = Path(backup_dir).expanduser()
    _assert_safe_mutation_paths(source_path, backup_root / f"{name}.bak", backup_root)
    fingerprint = fingerprint_local(source_path)
    if not fingerprint.get("ok"):
        raise ReleaseError("backup-source-invalid")
    target = backup_root / f"{name}.{fingerprint['sha256'][:16]}.bak"
    plan = {
        "ok": True,
        "status": "planned" if not apply else "backed-up",
        "name": name,
        "sha256": fingerprint["sha256"],
        "wouldWrite": not apply,
    }
    if not apply:
        return plan
    if confirmation != "BACKUP":
        raise ReleaseError("backup-confirmation-required")
    if backup_root.exists():
        info = backup_root.lstat()
        if not stat.S_ISDIR(info.st_mode) or backup_root.is_symlink():
            raise ReleaseError("backup-root-invalid")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise ReleaseError("backup-root-permissions-invalid")
    else:
        backup_root.mkdir(parents=True, mode=0o700)
        os.chmod(backup_root, 0o700)
    if target.exists():
        raise ReleaseError("backup-already-exists")
    with source_path.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())
    os.chmod(target, 0o600)
    if sha256_file(target) != fingerprint["sha256"]:
        raise ReleaseError("backup-checksum-mismatch")
    return plan


def install_artifact(
    source: Path | str,
    destination: Path | str,
    backup_dir: Path | str,
    *,
    name: str,
    expected_sha256: str,
    apply: bool = False,
    confirmation: str = "",
) -> dict[str, Any]:
    if not SAFE_NAME_RE.fullmatch(name):
        raise ReleaseError("invalid-artifact-name")
    source_path = Path(source).expanduser()
    destination_path = Path(destination).expanduser()
    backup_root = Path(backup_dir).expanduser()
    _assert_safe_mutation_paths(source_path, destination_path, backup_root)
    if destination_path.is_symlink():
        raise ReleaseError("install-destination-symlink")
    source_fingerprint = fingerprint_local(source_path)
    if not source_fingerprint.get("ok"):
        raise ReleaseError("install-source-invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_sha256 or "")):
        raise ReleaseError("expected-checksum-invalid")
    if source_fingerprint["sha256"] != expected_sha256:
        raise ReleaseError("install-source-checksum-mismatch")
    prior = fingerprint_local(destination_path)
    plan = {
        "ok": True,
        "status": "planned" if not apply else "installed",
        "name": name,
        "sourceChecksum": source_fingerprint["sha256"],
        "priorChecksum": prior.get("sha256", ""),
        "wouldBackup": bool(prior.get("ok")),
        "wouldWrite": not apply,
    }
    if not apply:
        return plan
    if confirmation != "INSTALL":
        raise ReleaseError("install-confirmation-required")
    if prior.get("ok"):
        backup_artifact(
            destination_path,
            backup_root,
            name=f"{name}-preinstall",
            apply=True,
            confirmation="BACKUP",
        )
    elif destination_path.exists():
        raise ReleaseError("install-destination-invalid")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(f".{destination_path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise ReleaseError("install-temporary-collision")
    try:
        with source_path.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        mode = stat.S_IMODE(destination_path.stat().st_mode) if destination_path.exists() else stat.S_IMODE(source_path.stat().st_mode)
        os.chmod(temporary, mode)
        os.replace(temporary, destination_path)
        directory_fd = os.open(destination_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    if sha256_file(destination_path) != expected_sha256:
        raise ReleaseError("installed-checksum-mismatch")
    return plan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    preflight = sub.add_parser("preflight")
    preflight.add_argument("--manifest", required=True)
    preflight.add_argument("--rollout", required=True)
    preflight.add_argument("--cutover-at")
    preflight.add_argument("--brain-fixture-attestation")

    transition = sub.add_parser("transition")
    transition.add_argument("--from-state", required=True, choices=ROLLOUT_SEQUENCE)
    transition.add_argument("--to-state", required=True, choices=ROLLOUT_SEQUENCE)

    backup = sub.add_parser("backup")
    backup.add_argument("--source", required=True)
    backup.add_argument("--backup-dir", required=True)
    backup.add_argument("--name", required=True)
    backup.add_argument("--apply", action="store_true")
    backup.add_argument("--confirm", default="")

    install = sub.add_parser("install")
    install.add_argument("--source", required=True)
    install.add_argument("--destination", required=True)
    install.add_argument("--backup-dir", required=True)
    install.add_argument("--name", required=True)
    install.add_argument("--expected-sha256", required=True)
    install.add_argument("--apply", action="store_true")
    install.add_argument("--confirm", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preflight":
            result = release_preflight(
                load_json_object(args.manifest),
                load_json_object(args.rollout),
                cutover_at=args.cutover_at,
                brain_attestation_path=args.brain_fixture_attestation,
            )
        elif args.command == "transition":
            result = validate_transition(args.from_state, args.to_state)
        elif args.command == "backup":
            result = backup_artifact(
                args.source,
                args.backup_dir,
                name=args.name,
                apply=args.apply,
                confirmation=args.confirm,
            )
        else:
            result = install_artifact(
                args.source,
                args.destination,
                args.backup_dir,
                name=args.name,
                expected_sha256=args.expected_sha256,
                apply=args.apply,
                confirmation=args.confirm,
            )
    except ReleaseError as exc:
        result = {"ok": False, "status": "blocked", "error": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
