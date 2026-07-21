#!/usr/bin/env python3
"""Generate controlled offline Telegram shadow evidence without Telegram I/O.

This harness is intentionally distinct from live shadow observations.  It runs
one fixed, dashboard-safe corpus for exactly one owner, exercises the production
classifier, renderers, and public lifecycle APIs, and records the resulting
surface observations through an append-only in-memory transport.  The caller
cannot supply an observed contract or a delivery result.

The resulting rows live in the normal private lifecycle database so the normal
read-only release inventory can audit them.  A separate 0600 HMAC attestation
binds those rows to the corpus and the exact lifecycle/Josh/JAIMES adapter code
used for the run.  These rows are controlled offline evidence, never live
Telegram samples.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import hmac
import json
import os
import socket
import sqlite3
import stat
import subprocess
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from telegram_gateway_lifecycle import (
    ALLOWED_OWNERS,
    CLASSIFIER_VERSION,
    LIFECYCLE_VERSION,
    RENDERER_VERSION,
    GatewayLifecycle,
    LifecycleError,
    RolloutPolicy,
    canonical_work_id,
    classify_delivery_tier,
    payload_hash,
    render_final,
    render_live_card,
    stable_id,
)


SCHEMA_VERSION = 1
CORPUS_VERSION = "jcu10-controlled-shadow-v1"
EVIDENCE_KIND = "controlled-offline-shadow-evidence"
MINIMUM_CASES = 20
SOURCE_FILENAMES = (
    "telegram_gateway_lifecycle.py",
    "josh_telegram_fast_ack.py",
    "jaimes_telegram_fast_ack.py",
)
ADAPTER_FUNCTIONS = {
    "josh_telegram_fast_ack.py": (
        "send_ack",
        "prepare_lifecycle_terminal",
        "finish_lifecycle_terminal",
    ),
    "jaimes_telegram_fast_ack.py": (
        "send_ack",
        "prepare_terminal_response",
        "finish_shadow_terminal_delivery",
    ),
}
SURFACE_BY_TIER = {
    1: ("final",),
    2: ("reaction", "final"),
    3: ("reaction", "card", "final"),
}
CONTRACT_BY_SURFACE = {
    ("final",): "final-only",
    ("reaction", "final"): "reaction-final",
    ("reaction", "card", "final"): "reaction-card-final",
}


class ShadowFixtureError(RuntimeError):
    """Fail-closed controlled shadow evidence error."""


@dataclass(frozen=True)
class FixtureCase:
    case_id: str
    prompt: str
    expected_tier: int
    expected_reason: str


# Fixed public/synthetic text only.  Eight cases per tier exercise all three
# surface contracts while keeping the minimum impossible to lower at runtime.
FIXED_CORPUS: tuple[FixtureCase, ...] = (
    FixtureCase("conversation-01", "hello", 1, "conversation"),
    FixtureCase("conversation-02", "thanks", 1, "conversation"),
    FixtureCase("conversation-03", "okay", 1, "conversation"),
    FixtureCase("conversation-04", "good morning", 1, "conversation"),
    FixtureCase("conversation-05", "cool", 1, "conversation"),
    FixtureCase("conversation-06", "great", 1, "conversation"),
    FixtureCase("conversation-07", "nice", 1, "conversation"),
    FixtureCase("conversation-08", "thank you!", 1, "conversation"),
    FixtureCase("quick-01", "What color is the daytime sky?", 2, "quick-answer"),
    FixtureCase("quick-02", "Who wrote Hamlet?", 2, "quick-answer"),
    FixtureCase("quick-03", "When does spring begin?", 2, "quick-answer"),
    FixtureCase("quick-04", "Where is Lisbon?", 2, "quick-answer"),
    FixtureCase("quick-05", "Why do leaves look green?", 2, "quick-answer"),
    FixtureCase("quick-06", "How many minutes are in an hour?", 2, "quick-answer"),
    FixtureCase("quick-07", "Is water clear?", 2, "quick-answer"),
    FixtureCase("quick-08", "Are triangles polygons?", 2, "quick-answer"),
    FixtureCase("complex-01", "Please verify the current synthetic fixture.", 3, "multi-step"),
    FixtureCase("complex-02", "Create a bounded synthetic checklist.", 3, "multi-step"),
    FixtureCase("complex-03", "Compare two public placeholder values.", 3, "multi-step"),
    FixtureCase("complex-04", "Investigate the deterministic example.", 3, "multi-step"),
    FixtureCase("complex-05", "Research a public test convention.", 3, "multi-step"),
    FixtureCase("complex-06", "Test the fixed offline renderer contract.", 3, "multi-step"),
    FixtureCase("complex-07", "Implement a synthetic no-op plan.", 3, "multi-step"),
    FixtureCase("complex-08", "Analyze the bounded fixture result.", 3, "multi-step"),
)

if len(FIXED_CORPUS) < MINIMUM_CASES:
    raise RuntimeError("controlled-shadow-corpus-below-floor")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def corpus_digest() -> str:
    return sha256_bytes(
        canonical_json([
            {
                "caseId": case.case_id,
                "promptHash": sha256_bytes(case.prompt.encode("utf-8")),
                "expectedTier": case.expected_tier,
                "expectedReason": case.expected_reason,
            }
            for case in FIXED_CORPUS
        ])
    )


def callable_digest(function: Callable[..., Any]) -> str:
    code = getattr(function, "__code__", None)
    if code is None:
        raise ShadowFixtureError("loaded-callable-unavailable")

    def normalize(value: Any) -> Any:
        if isinstance(value, types.CodeType):
            return {
                "argcount": value.co_argcount,
                "kwonlyargcount": value.co_kwonlyargcount,
                "nlocals": value.co_nlocals,
                "flags": value.co_flags,
                "code": value.co_code.hex(),
                "consts": [normalize(item) for item in value.co_consts],
                "names": list(value.co_names),
                "varnames": list(value.co_varnames),
                "freevars": list(value.co_freevars),
                "cellvars": list(value.co_cellvars),
            }
        if value is None or isinstance(value, (bool, int, float, str, bytes)):
            return value.hex() if isinstance(value, bytes) else value
        if isinstance(value, tuple):
            return [normalize(item) for item in value]
        if isinstance(value, (set, frozenset)):
            items = [normalize(item) for item in value]
            return {
                "type": type(value).__name__,
                "items": sorted(items, key=canonical_json),
            }
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            items = [
                [normalize(key), normalize(item)]
                for key, item in value.items()
            ]
            return {
                "type": "dict",
                "items": sorted(items, key=canonical_json),
            }
        return {"type": type(value).__name__, "value": str(value)}

    return sha256_bytes(canonical_json(normalize(code)))


def loaded_callable_digests() -> dict[str, str]:
    return {
        "classifyDeliveryTier": callable_digest(classify_delivery_tier),
        "renderLiveCard": callable_digest(render_live_card),
        "renderFinal": callable_digest(render_final),
        "startWork": callable_digest(GatewayLifecycle.start_work),
        "recordShadowSample": callable_digest(GatewayLifecycle.record_shadow_sample),
        "finishShadowSample": callable_digest(GatewayLifecycle.finish_shadow_sample),
    }


def source_scripts_dir(source_root: Path | None = None) -> Path:
    return (source_root or Path(__file__).resolve().parents[1]).resolve() / "scripts"


def implementation_digests(source_root: Path | None = None) -> dict[str, str]:
    scripts = source_scripts_dir(source_root)
    result: dict[str, str] = {}
    for filename in SOURCE_FILENAMES:
        path = scripts / filename
        if not path.is_file() or path.is_symlink():
            raise ShadowFixtureError("implementation-source-unavailable")
        result[filename] = sha256_file(path)
    harness = scripts / Path(__file__).name
    if harness.is_file() and not harness.is_symlink():
        result[Path(__file__).name] = sha256_file(harness)
    return result


def _function_ast(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise ShadowFixtureError("adapter-contract-function-missing")


def adapter_contract_digests(source_root: Path | None = None) -> dict[str, str]:
    """Fingerprint the exact live adapter branches shadow evidence depends on."""
    scripts = source_scripts_dir(source_root)
    result: dict[str, str] = {}
    for filename, function_names in ADAPTER_FUNCTIONS.items():
        path = scripts / filename
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            raise ShadowFixtureError("adapter-contract-source-invalid") from exc
        nodes = [_function_ast(tree, name) for name in function_names]
        send_constants = {
            node.value
            for node in ast.walk(nodes[0])
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        if not set(CONTRACT_BY_SURFACE.values()).issubset(send_constants):
            raise ShadowFixtureError("adapter-contract-literals-missing")
        call_names = {
            node.func.attr
            for function in nodes
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        if "record_shadow_sample" not in call_names or "finish_shadow_sample" not in call_names:
            raise ShadowFixtureError("adapter-shadow-observation-hooks-missing")
        normalized = [ast.dump(node, annotate_fields=True, include_attributes=False) for node in nodes]
        result[filename] = sha256_bytes(canonical_json(normalized))
    return result


@dataclass(frozen=True)
class OfflineReceipt:
    sequence: int
    kind: str
    payload_digest: str


class OfflineTransportRecorder:
    """Append-only transport sink with no network or Telegram implementation."""

    def __init__(self) -> None:
        self._receipts: list[OfflineReceipt] = []
        self._closed = False

    def emit(self, kind: str, payload: str) -> OfflineReceipt:
        if self._closed:
            raise ShadowFixtureError("offline-transport-already-closed")
        if kind not in {"reaction", "card", "final"}:
            raise ShadowFixtureError("offline-transport-kind-invalid")
        receipt = OfflineReceipt(
            sequence=len(self._receipts) + 1,
            kind=kind,
            payload_digest=sha256_bytes(str(payload).encode("utf-8")),
        )
        self._receipts.append(receipt)
        return receipt

    def close(self) -> tuple[OfflineReceipt, ...]:
        self._closed = True
        if not self._receipts or self._receipts[-1].kind != "final":
            raise ShadowFixtureError("offline-terminal-receipt-missing")
        return tuple(self._receipts)

    @property
    def terminal_confirmed(self) -> bool:
        return bool(self._closed and self._receipts and self._receipts[-1].kind == "final")


@dataclass
class ExternalEffectGuard:
    attempts: int = 0

    def blocked(self, *_args: Any, **_kwargs: Any) -> Any:
        self.attempts += 1
        raise ShadowFixtureError("external-effect-attempted")


@contextlib.contextmanager
def deny_external_effects() -> Iterator[ExternalEffectGuard]:
    """Deny network and child-process entrypoints during every fixture case."""
    guard = ExternalEffectGuard()
    replacements = (
        (socket, "socket"),
        (socket, "create_connection"),
        (subprocess, "Popen"),
        (subprocess, "run"),
        (subprocess, "call"),
        (subprocess, "check_call"),
        (subprocess, "check_output"),
        (os, "system"),
    )
    originals: list[tuple[Any, str, Any]] = []
    try:
        for module, name in replacements:
            originals.append((module, name, getattr(module, name)))
            setattr(module, name, guard.blocked)
        yield guard
    finally:
        for module, name, original in reversed(originals):
            setattr(module, name, original)


def _observed_contract(receipts: Sequence[OfflineReceipt]) -> str:
    ordered = tuple(receipt.kind for receipt in receipts)
    contract = CONTRACT_BY_SURFACE.get(ordered)
    if not contract:
        raise ShadowFixtureError("offline-surface-contract-invalid")
    return contract


def _simulate_adapter(
    receipt: Mapping[str, Any],
    *,
    card_html: str,
    final_html: str,
) -> tuple[OfflineTransportRecorder, tuple[OfflineReceipt, ...]]:
    tier = int(receipt.get("deliveryTier") or 0)
    plan = SURFACE_BY_TIER.get(tier)
    if not plan:
        raise ShadowFixtureError("offline-delivery-tier-invalid")
    recorder = OfflineTransportRecorder()
    for kind in plan:
        if kind == "reaction":
            recorder.emit(kind, "eyes-confirmed-offline")
        elif kind == "card":
            if not card_html:
                raise ShadowFixtureError("offline-card-render-missing")
            recorder.emit(kind, card_html)
        else:
            recorder.emit(kind, final_html)
    return recorder, recorder.close()


def _case_material(
    owner: str,
    case: FixtureCase,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    card_html = ""
    if int(receipt.get("deliveryTier") or 0) == 3:
        card_html = render_live_card(
            receipt,
            objective="Exercise the controlled offline Telegram shadow contract",
            phase_label="Working",
            model="offline-fixture",
            route=f"{owner}/controlled-shadow",
            progress=50,
        )
    final_html = render_final(
        model="offline-fixture",
        route=f"{owner}/controlled-shadow",
        why="fixed controlled offline evidence",
        complete="Yes",
        done=("Exercised one fixed shadow contract case.",),
        issues=("n/a",),
        next_steps=("Use normal read-only release inventory.",),
        approvals=("n/a",),
    )
    with deny_external_effects() as guard:
        recorder, transport_receipts = _simulate_adapter(
            receipt,
            card_html=card_html,
            final_html=final_html,
        )
    if guard.attempts:
        raise ShadowFixtureError("external-effect-attempted")
    transport_rows = [
        {
            "sequence": item.sequence,
            "kind": item.kind,
            "payloadDigest": item.payload_digest,
        }
        for item in transport_receipts
    ]
    terminal_payload = {
        "format": "controlled-offline-shadow-v1",
        "caseId": case.case_id,
        "cardDigest": sha256_bytes(card_html.encode("utf-8")) if card_html else "",
        "finalDigest": sha256_bytes(final_html.encode("utf-8")),
        "surfaceReceiptDigest": sha256_bytes(canonical_json(transport_rows)),
    }
    return {
        "cardHtml": card_html,
        "finalHtml": final_html,
        "recorder": recorder,
        "transportReceipts": transport_receipts,
        "observedContract": _observed_contract(transport_receipts),
        "terminalPayload": terminal_payload,
        "externalEffectAttempts": guard.attempts,
    }


def _case_identity(owner: str, case: FixtureCase) -> tuple[str, str, str, str]:
    corpus = corpus_digest()[:16]
    origin = f"jcu10-controlled-shadow:{CORPUS_VERSION}:{corpus}:{owner}:{case.case_id}"
    run_id = f"controlled-shadow-{corpus}-{owner}-{case.case_id}"
    work_id = canonical_work_id(origin, run_id)
    sample_id = stable_id("shadow", owner, work_id, length=28)
    return origin, run_id, work_id, sample_id


def _assert_shadow_policy(policy: RolloutPolicy, owner: str) -> None:
    policy.validate()
    if owner not in ALLOWED_OWNERS:
        raise ShadowFixtureError("controlled-shadow-owner-invalid")
    if policy.master_state != "shadow":
        raise ShadowFixtureError("controlled-shadow-policy-required")
    if not policy.shadow_enabled(owner) or policy.writer_enabled(owner):
        raise ShadowFixtureError("controlled-shadow-policy-required")


def _assert_private_file(path: Path, *, label: str) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ShadowFixtureError(f"{label}-missing") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ShadowFixtureError(f"{label}-invalid")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ShadowFixtureError(f"{label}-permissions-invalid")
    return path.read_bytes()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_private_parent(path: Path) -> None:
    parent = path.parent
    if parent.exists():
        info = parent.lstat()
        if parent.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise ShadowFixtureError("private-parent-invalid")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise ShadowFixtureError("private-parent-permissions-invalid")
    else:
        parent.mkdir(parents=True, mode=0o700)
        os.chmod(parent, 0o700)


def prepare_private_lifecycle_root(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise ShadowFixtureError("lifecycle-root-invalid")
    _ensure_private_parent(candidate)
    if candidate.exists():
        info = candidate.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
            raise ShadowFixtureError("lifecycle-root-invalid")
        database = candidate / "lifecycle.sqlite3"
        if database.exists() or database.is_symlink():
            db_info = database.lstat()
            if (
                database.is_symlink()
                or not stat.S_ISREG(db_info.st_mode)
                or stat.S_IMODE(db_info.st_mode) != 0o600
            ):
                raise ShadowFixtureError("lifecycle-database-invalid")
    return candidate.resolve()


def load_or_create_hmac_key(path: Path) -> bytes:
    _ensure_private_parent(path)
    if path.exists() or path.is_symlink():
        key = _assert_private_file(path, label="attestation-key")
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            key = os.urandom(32)
            os.write(descriptor, key)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    if len(key) < 32:
        raise ShadowFixtureError("attestation-key-too-short")
    return key


def atomic_write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    _ensure_private_parent(path)
    if path.is_symlink():
        raise ShadowFixtureError("attestation-path-invalid")
    if path.exists():
        _assert_private_file(path, label="attestation")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _advance_to_terminal(
    lifecycle: GatewayLifecycle,
    receipt: Mapping[str, Any],
    *,
    terminal_payload: Mapping[str, Any],
) -> dict[str, Any]:
    current = dict(receipt)
    transitions = {
        "received": "classified",
        "classified": "acknowledged",
        "acknowledged": "working",
        "working": "verifying",
        "awaiting_input": "verifying",
    }
    while str(current.get("phase") or "") in transitions:
        current = lifecycle.transition(
            str(current["workId"]),
            transitions[str(current["phase"])],
            expected_sequence=int(current["sequence"]),
            fencing_epoch=int(current["fencingEpoch"]),
            safe_payload={"status": "controlled_offline_shadow"},
        )
    if str(current.get("phase") or "") == "verifying":
        lifecycle.commit_terminal(
            str(current["workId"]),
            "succeeded",
            expected_sequence=int(current["sequence"]),
            fencing_epoch=int(current["fencingEpoch"]),
            private_payload=terminal_payload,
        )
        current = lifecycle.read_work(str(current["workId"])) or current
    if str(current.get("phase") or "") != "terminal":
        raise ShadowFixtureError("controlled-shadow-terminal-commit-failed")
    with lifecycle.connect() as db:
        outbox = db.execute(
            "SELECT payload_json,payload_hash FROM terminal_outbox WHERE work_id=?",
            (str(current["workId"]),),
        ).fetchone()
    if not outbox:
        raise ShadowFixtureError("controlled-shadow-terminal-outbox-missing")
    try:
        durable_payload = json.loads(str(outbox["payload_json"]))
    except json.JSONDecodeError as exc:
        raise ShadowFixtureError("controlled-shadow-terminal-payload-invalid") from exc
    if (
        durable_payload != dict(terminal_payload)
        or not hmac.compare_digest(
            str(outbox["payload_hash"]),
            payload_hash(terminal_payload),
        )
    ):
        raise ShadowFixtureError("controlled-shadow-terminal-payload-conflict")
    return current


def _assert_existing_sample_compatible(
    lifecycle: GatewayLifecycle,
    *,
    owner: str,
    work_id: str,
    sample_id: str,
    observed_contract: str,
) -> None:
    with lifecycle.connect() as db:
        rows = db.execute(
            "SELECT id,owner,work_id,legacy_contract,matched,terminal_observed,terminal_delivered "
            "FROM shadow_samples WHERE id=? OR (owner=? AND work_id=?)",
            (sample_id, owner, work_id),
        ).fetchall()
    for row in rows:
        identity_ok = (
            hmac.compare_digest(str(row["id"]), sample_id)
            and hmac.compare_digest(str(row["owner"]), owner)
            and hmac.compare_digest(str(row["work_id"]), work_id)
        )
        observation_ok = hmac.compare_digest(str(row["legacy_contract"]), observed_contract)
        terminal_conflict = bool(
            int(row["terminal_observed"]) == 1
            and int(row["terminal_delivered"]) != 1
        )
        if (
            not identity_ok
            or not observation_ok
            or int(row["matched"]) != 1
            or terminal_conflict
        ):
            raise ShadowFixtureError("controlled-shadow-existing-observation-conflict")


def _assert_owner_scope_clean(lifecycle: GatewayLifecycle, owner: str, expected: set[str]) -> None:
    with lifecycle.connect() as db:
        wrong_owner = int(db.execute(
            "SELECT COUNT(*) FROM shadow_samples WHERE owner!=?",
            (owner,),
        ).fetchone()[0]) + int(db.execute(
            "SELECT COUNT(*) FROM work_receipts WHERE shadow_only=1 AND current_owner!=?",
            (owner,),
        ).fetchone()[0])
        dirty_external = int(db.execute(
            "SELECT COUNT(*) FROM shadow_samples WHERE owner=? AND work_id NOT IN ("
            + ",".join("?" for _ in expected)
            + ") AND (matched!=1 OR terminal_observed!=1 OR terminal_delivered!=1)",
            (owner, *sorted(expected)),
        ).fetchone()[0])
        open_external = int(db.execute(
            "SELECT COUNT(*) FROM work_receipts WHERE shadow_only=1 AND current_owner=? "
            "AND work_id NOT IN (" + ",".join("?" for _ in expected) + ") AND phase!='terminal'",
            (owner, *sorted(expected)),
        ).fetchone()[0])
    if wrong_owner:
        raise ShadowFixtureError("controlled-shadow-owner-scope-conflict")
    if dirty_external or open_external:
        raise ShadowFixtureError("controlled-shadow-existing-evidence-unclean")


def _evidence_row(
    lifecycle: GatewayLifecycle,
    *,
    owner: str,
    case: FixtureCase,
    work_id: str,
    sample_id: str,
    observed_contract: str,
    receipts: Sequence[OfflineReceipt],
    card_digest: str,
    final_digest: str,
) -> dict[str, Any]:
    with lifecycle.connect() as db:
        work = db.execute(
            "SELECT * FROM work_receipts WHERE work_id=?", (work_id,),
        ).fetchone()
        sample = db.execute(
            "SELECT * FROM shadow_samples WHERE id=?", (sample_id,),
        ).fetchone()
        outbox = db.execute(
            "SELECT * FROM terminal_outbox WHERE work_id=?", (work_id,),
        ).fetchone()
        effects = int(db.execute(
            "SELECT COUNT(*) FROM effects WHERE work_id=?", (work_id,),
        ).fetchone()[0])
    if not work or not sample or not outbox:
        raise ShadowFixtureError("controlled-shadow-durable-evidence-missing")
    if (
        str(work["phase"]) != "terminal"
        or int(work["shadow_only"]) != 1
        or str(work["current_owner"]) != owner
        or int(sample["matched"]) != 1
        or int(sample["terminal_observed"]) != 1
        or int(sample["terminal_delivered"]) != 1
        or effects != 0
    ):
        raise ShadowFixtureError("controlled-shadow-durable-evidence-unclean")
    event_rows = [
        {
            "sequence": receipt.sequence,
            "kind": receipt.kind,
            "payloadDigest": receipt.payload_digest,
        }
        for receipt in receipts
    ]
    return {
        "caseId": case.case_id,
        "workId": work_id,
        "sampleId": sample_id,
        "tier": int(work["delivery_tier"]),
        "reason": str(work["classifier_reason"]),
        "observedContract": observed_contract,
        "surfaceReceiptDigest": sha256_bytes(canonical_json(event_rows)),
        "cardDigest": card_digest,
        "finalDigest": final_digest,
        "terminalPayloadHash": str(outbox["payload_hash"]),
        "matched": True,
        "terminalObserved": True,
        "terminalDelivered": True,
        "liveTelegramSample": False,
    }


def run_controlled_shadow_evidence(
    *,
    owner: str,
    lifecycle_root: Path,
    rollout_path: Path,
    attestation_path: Path,
    key_path: Path,
    source_root: Path | None = None,
) -> dict[str, Any]:
    owner = str(owner or "")
    policy = RolloutPolicy.load(rollout_path)
    _assert_shadow_policy(policy, owner)
    key = load_or_create_hmac_key(key_path)
    _ensure_private_parent(attestation_path)
    if attestation_path.exists() or attestation_path.is_symlink():
        _assert_private_file(attestation_path, label="attestation")
    lifecycle_root = prepare_private_lifecycle_root(lifecycle_root)
    lifecycle = GatewayLifecycle(lifecycle_root, rollout=policy, owner=owner)
    identities = {
        _case_identity(owner, case)[2]
        for case in FIXED_CORPUS
    }
    _assert_owner_scope_clean(lifecycle, owner, identities)
    source_digests = implementation_digests(source_root)
    adapter_digests = adapter_contract_digests(source_root)
    memory_digests = loaded_callable_digests()
    rows: list[dict[str, Any]] = []
    total_external_attempts = 0

    for case in FIXED_CORPUS:
        classification = classify_delivery_tier(case.prompt)
        if classification != (case.expected_tier, case.expected_reason):
            raise ShadowFixtureError("controlled-shadow-classifier-drift")
        origin, run_id, work_id, sample_id = _case_identity(owner, case)
        receipt = lifecycle.start_work(
            origin_key=origin,
            run_id=run_id,
            work_id=work_id,
            intake_agent=owner,
            current_owner=owner,
            surface_contract="telegram",
            text="",
            worker_route="controlled-offline-shadow",
            classification=classification,
        )
        if not receipt.get("shadowOnly") or receipt.get("writerEnabled"):
            raise ShadowFixtureError("controlled-shadow-receipt-not-shadow-only")
        material = _case_material(owner, case, receipt)
        card_html = str(material["cardHtml"])
        if card_html:
            lifecycle.update_render_hash(work_id, card_html)
        recorder = material["recorder"]
        transport_receipts = material["transportReceipts"]
        total_external_attempts += int(material["externalEffectAttempts"])
        observed_contract = str(material["observedContract"])
        _assert_existing_sample_compatible(
            lifecycle,
            owner=owner,
            work_id=work_id,
            sample_id=sample_id,
            observed_contract=observed_contract,
        )
        lifecycle.record_shadow_sample(
            work_id,
            observed_contract=observed_contract,
        )
        terminal_payload = material["terminalPayload"]
        terminal = _advance_to_terminal(
            lifecycle,
            receipt,
            terminal_payload=terminal_payload,
        )
        if not recorder.terminal_confirmed:
            raise ShadowFixtureError("controlled-shadow-terminal-receipt-missing")
        lifecycle.finish_shadow_sample(work_id, delivered=recorder.terminal_confirmed)
        rows.append(_evidence_row(
            lifecycle,
            owner=owner,
            case=case,
            work_id=work_id,
            sample_id=sample_id,
            observed_contract=observed_contract,
            receipts=transport_receipts,
            card_digest=terminal_payload["cardDigest"],
            final_digest=terminal_payload["finalDigest"],
        ))
        if terminal.get("phase") != "terminal":
            raise ShadowFixtureError("controlled-shadow-terminal-state-missing")

    if total_external_attempts:
        raise ShadowFixtureError("external-effect-attempted")
    if len(rows) < MINIMUM_CASES or not all(
        row["matched"] and row["terminalObserved"] and row["terminalDelivered"]
        for row in rows
    ):
        raise ShadowFixtureError("controlled-shadow-evidence-floor-not-met")
    counts_by_tier = {
        str(tier): sum(int(row["tier"]) == tier for row in rows)
        for tier in sorted(SURFACE_BY_TIER)
    }
    body: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "evidenceKind": EVIDENCE_KIND,
        "corpusVersion": CORPUS_VERSION,
        "corpusDigest": corpus_digest(),
        "owner": owner,
        "lifecycleVersion": LIFECYCLE_VERSION,
        "classifierVersion": CLASSIFIER_VERSION,
        "rendererVersion": RENDERER_VERSION,
        "sampleCount": len(rows),
        "minimumRequired": MINIMUM_CASES,
        "cleanSampleCount": len(rows),
        "countsByTier": counts_by_tier,
        "controlledOffline": True,
        "liveTelegramSamples": False,
        "telegramHelperCalls": 0,
        "externalEffectAttempts": total_external_attempts,
        "implementationDigests": source_digests,
        "adapterContractDigests": adapter_digests,
        "loadedCallableDigests": memory_digests,
        "cases": rows,
        "evidenceDigest": sha256_bytes(canonical_json(rows)),
    }
    attestation = dict(body)
    attestation["signature"] = {
        "algorithm": "hmac-sha256",
        "value": hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest(),
    }
    atomic_write_private_json(attestation_path, attestation)
    verified = verify_attestation(
        attestation_path=attestation_path,
        key_path=key_path,
        lifecycle_root=lifecycle_root,
        source_root=source_root,
    )
    if not verified.get("ok"):
        safe_problems = ",".join(str(item) for item in verified.get("problems") or [])
        raise ShadowFixtureError(
            f"controlled-shadow-attestation-verification-failed:{safe_problems}"
        )
    return verified


def _read_attestation(path: Path) -> dict[str, Any]:
    raw = _assert_private_file(path, label="attestation")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ShadowFixtureError("attestation-json-invalid") from exc
    if not isinstance(value, dict):
        raise ShadowFixtureError("attestation-json-shape-invalid")
    return value


def verify_attestation(
    *,
    attestation_path: Path,
    key_path: Path,
    lifecycle_root: Path,
    source_root: Path | None = None,
) -> dict[str, Any]:
    problems: list[str] = []
    try:
        attestation = _read_attestation(attestation_path)
        key = _assert_private_file(key_path, label="attestation-key")
    except ShadowFixtureError as exc:
        return {"ok": False, "status": "blocked", "problems": [str(exc)]}
    if len(key) < 32:
        problems.append("attestation-key-too-short")
    signature = attestation.get("signature")
    body = {key_name: value for key_name, value in attestation.items() if key_name != "signature"}
    expected_signature = hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()
    if not isinstance(signature, dict) or signature.get("algorithm") != "hmac-sha256":
        problems.append("attestation-signature-invalid")
    elif not hmac.compare_digest(str(signature.get("value") or ""), expected_signature):
        problems.append("attestation-signature-mismatch")
    if body.get("schemaVersion") != SCHEMA_VERSION or body.get("evidenceKind") != EVIDENCE_KIND:
        problems.append("attestation-schema-invalid")
    if body.get("corpusVersion") != CORPUS_VERSION or body.get("corpusDigest") != corpus_digest():
        problems.append("attestation-corpus-drift")
    if (
        body.get("lifecycleVersion") != LIFECYCLE_VERSION
        or body.get("classifierVersion") != CLASSIFIER_VERSION
        or body.get("rendererVersion") != RENDERER_VERSION
    ):
        problems.append("attestation-contract-version-drift")
    owner = str(body.get("owner") or "")
    if owner not in ALLOWED_OWNERS:
        problems.append("attestation-owner-invalid")
    if body.get("controlledOffline") is not True or body.get("liveTelegramSamples") is not False:
        problems.append("attestation-evidence-label-invalid")
    if int(body.get("telegramHelperCalls") or 0) != 0 or int(body.get("externalEffectAttempts") or 0) != 0:
        problems.append("attestation-external-effect-invalid")
    try:
        if body.get("implementationDigests") != implementation_digests(source_root):
            problems.append("attestation-implementation-drift")
        if body.get("adapterContractDigests") != adapter_contract_digests(source_root):
            problems.append("attestation-adapter-contract-drift")
        current_loaded = loaded_callable_digests()
        stored_loaded = (
            body.get("loadedCallableDigests")
            if isinstance(body.get("loadedCallableDigests"), dict)
            else {}
        )
        for name in sorted(set(current_loaded) | set(stored_loaded)):
            if stored_loaded.get(name) != current_loaded.get(name):
                problems.append(f"attestation-loaded-code-drift:{name}")
    except ShadowFixtureError:
        problems.append("attestation-implementation-unavailable")
    cases = body.get("cases") if isinstance(body.get("cases"), list) else []
    if (
        int(body.get("sampleCount") or 0) != len(FIXED_CORPUS)
        or int(body.get("cleanSampleCount") or 0) != len(FIXED_CORPUS)
        or len(cases) != len(FIXED_CORPUS)
        or len(cases) < MINIMUM_CASES
    ):
        problems.append("attestation-sample-count-invalid")
    expected_counts = {
        str(tier): sum(case.expected_tier == tier for case in FIXED_CORPUS)
        for tier in sorted(SURFACE_BY_TIER)
    }
    if body.get("countsByTier") != expected_counts or body.get("minimumRequired") != MINIMUM_CASES:
        problems.append("attestation-tier-coverage-invalid")
    if body.get("evidenceDigest") != sha256_bytes(canonical_json(cases)):
        problems.append("attestation-evidence-digest-invalid")

    expected_by_id = {case.case_id: case for case in FIXED_CORPUS}
    seen: set[str] = set()
    lifecycle_candidate = lifecycle_root.expanduser()
    try:
        root_info = lifecycle_candidate.lstat()
        if (
            lifecycle_candidate.is_symlink()
            or not stat.S_ISDIR(root_info.st_mode)
            or stat.S_IMODE(root_info.st_mode) != 0o700
        ):
            raise OSError("invalid")
        database = lifecycle_candidate / "lifecycle.sqlite3"
        database_info = database.lstat()
        if (
            database.is_symlink()
            or not stat.S_ISREG(database_info.st_mode)
            or stat.S_IMODE(database_info.st_mode) != 0o600
        ):
            raise OSError("invalid")
    except OSError:
        problems.append("attestation-database-permissions-invalid")
        database = lifecycle_candidate / "lifecycle.sqlite3"
    try:
        uri = f"file:{database}?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        for row in cases:
            if not isinstance(row, dict):
                problems.append("attestation-case-shape-invalid")
                continue
            case_id = str(row.get("caseId") or "")
            case = expected_by_id.get(case_id)
            if not case or case_id in seen:
                problems.append("attestation-case-identity-invalid")
                continue
            seen.add(case_id)
            classification = classify_delivery_tier(case.prompt)
            if classification != (case.expected_tier, case.expected_reason):
                problems.append("attestation-classifier-drift")
                continue
            public_receipt = {
                "surfaceContract": "telegram",
                "deliveryTier": classification[0],
            }
            material = _case_material(owner, case, public_receipt)
            expected_contract = str(material["observedContract"])
            terminal_payload = material["terminalPayload"]
            _, _, work_id, sample_id = _case_identity(owner, case)
            if (
                row.get("workId") != work_id
                or row.get("sampleId") != sample_id
                or int(row.get("tier") or 0) != classification[0]
                or row.get("reason") != classification[1]
                or row.get("observedContract") != expected_contract
                or row.get("liveTelegramSample") is not False
                or row.get("matched") is not True
                or row.get("terminalObserved") is not True
                or row.get("terminalDelivered") is not True
                or row.get("surfaceReceiptDigest") != terminal_payload["surfaceReceiptDigest"]
                or row.get("cardDigest") != terminal_payload["cardDigest"]
                or row.get("finalDigest") != terminal_payload["finalDigest"]
                or row.get("terminalPayloadHash") != payload_hash(terminal_payload)
            ):
                problems.append("attestation-case-contract-invalid")
                continue
            work = db.execute(
                "SELECT * FROM work_receipts WHERE work_id=?", (work_id,),
            ).fetchone()
            sample = db.execute(
                "SELECT * FROM shadow_samples WHERE id=?", (sample_id,),
            ).fetchone()
            outbox = db.execute(
                "SELECT * FROM terminal_outbox WHERE work_id=?", (work_id,),
            ).fetchone()
            effects = int(db.execute(
                "SELECT COUNT(*) FROM effects WHERE work_id=?", (work_id,),
            ).fetchone()[0])
            if not work or not sample or not outbox:
                problems.append("attestation-database-row-missing")
                continue
            try:
                durable_terminal_payload = json.loads(str(outbox["payload_json"]))
            except json.JSONDecodeError:
                durable_terminal_payload = None
            if (
                str(work["phase"]) != "terminal"
                or int(work["shadow_only"]) != 1
                or str(work["current_owner"]) != owner
                or int(work["delivery_tier"]) != classification[0]
                or str(work["classifier_reason"]) != classification[1]
                or str(sample["owner"]) != owner
                or str(sample["work_id"]) != work_id
                or str(sample["legacy_contract"]) != expected_contract
                or int(sample["matched"]) != 1
                or int(sample["terminal_observed"]) != 1
                or int(sample["terminal_delivered"]) != 1
                or str(outbox["payload_hash"]) != str(row.get("terminalPayloadHash") or "")
                or durable_terminal_payload != terminal_payload
                or str(outbox["payload_hash"]) != payload_hash(terminal_payload)
                or str(work["render_hash"])
                != (
                    sha256_bytes(str(material["cardHtml"]).encode("utf-8"))
                    if material["cardHtml"]
                    else ""
                )
                or effects != 0
            ):
                problems.append("attestation-database-evidence-mismatch")
        if seen != set(expected_by_id):
            problems.append("attestation-corpus-incomplete")
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError):
        problems.append("attestation-database-unreadable")
    finally:
        try:
            db.close()
        except UnboundLocalError:
            pass
    return {
        "ok": not problems,
        "status": "verified" if not problems else "blocked",
        "evidenceKind": EVIDENCE_KIND,
        "owner": owner,
        "sampleCount": len(cases),
        "cleanSampleCount": len(cases) if not problems else 0,
        "liveTelegramSamples": False,
        "attestationDigest": sha256_bytes(canonical_json(attestation)),
        "problems": sorted(set(problems)),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--owner", choices=sorted(ALLOWED_OWNERS), required=True)
    run.add_argument("--lifecycle-root", type=Path, required=True)
    run.add_argument("--rollout", type=Path, required=True)
    run.add_argument("--attestation", type=Path, required=True)
    run.add_argument("--key", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--lifecycle-root", type=Path, required=True)
    verify.add_argument("--attestation", type=Path, required=True)
    verify.add_argument("--key", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "run":
        result = run_controlled_shadow_evidence(
            owner=args.owner,
            lifecycle_root=args.lifecycle_root,
            rollout_path=args.rollout,
            attestation_path=args.attestation,
            key_path=args.key,
        )
    else:
        result = verify_attestation(
            attestation_path=args.attestation,
            key_path=args.key,
            lifecycle_root=args.lifecycle_root,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
