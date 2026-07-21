#!/usr/bin/env python3
"""Governed, private Brain media intake owned by Josh 2.0.

The OpenCLAW gateway calls ``predownload`` after its durable ingress spool has
accepted a Telegram update and before any media download begins.  This module
then owns the private receipt, content-addressed storage, extraction evidence,
governance links, and source-bound Forget lifecycle.  It never treats media
content as instructions and emits only counts/status labels to shared surfaces.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence
from xml.etree import ElementTree


SCHEMA_VERSION = 6
LIFECYCLE_VERSION = 3
EXTRACTION_VERSION = "brain-local-v3"
OPENCLAW_VERSION = "2026.7.1"
OPENCLAW_INGRESS_ORIGINAL_SHA256 = "a4657f4f771fbc1b95f321c99c0ad89a181cb7df35493aae2d791674d2f015ac"
OPENCLAW_HOOK_PATCH_VERSION = 5

INGESTION_PHASES = {
    "receipt_pending", "downloading", "stored", "scanning", "extracting",
    "classifying", "deduplicating", "candidate_pending", "reviewing", "indexed",
    "unsupported", "quarantined", "forgotten",
}
TERMINAL_INGESTION_PHASES = {"indexed", "unsupported", "quarantined", "forgotten"}
PRIVACY_CLASSES = {"private", "internal", "dashboard-safe"}
AUTHORIZED_AGENTS = {"josh2", "jaimes", "jain", "joshex"}
AUTO_ELIGIBLE_TYPES = {"fact", "lesson", "entity", "relationship"}
MANUAL_REVIEW_TYPES = {"decision", "preference", "procedure", "episode", "policy", "instruction"}
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json", "application/xml", "text/xml"}
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_ARCHIVE_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ARCHIVE_RATIO = 100
MAX_EXTRACTED_CHARS = 2_000_000
MAX_ATTACHMENTS_PER_SUBMISSION = 20
MAX_SUBMISSION_BYTES = 250 * 1024 * 1024
FORGET_TTL_SECONDS = 600
DOWNLOAD_CLAIM_TTL_SECONDS = 120
WORKER_MAX_ATTEMPTS = 4
ATTACHMENT_FAILURE_REASONS = {
    "oversize": "unsupported",
    "corrupt": "quarantined",
    "download-unavailable": "unsupported",
}

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PROMPT_INJECTION_RE = re.compile(
    r"(?:ignore (?:all|any|the|previous|prior) instructions|system prompt|developer message|"
    r"you are (?:chatgpt|an? ai|the system)|execute (?:this|the following)|run (?:this|a) command|"
    r"call (?:the )?tool|reveal (?:a |the )?(?:secret|token|password|credential)|"
    r"override (?:policy|privacy|routing|approval)|BEGIN (?:SYSTEM|INSTRUCTION))",
    re.I,
)
UNSAFE_LINK_RE = re.compile(r"(?:javascript:|file:|data:text/html|https?://)", re.I)
CANDIDATE_LINE_RE = re.compile(
    r"^\s*(fact|date|lesson|entity|relationship|decision|preference|procedure|policy|instruction|episode)"
    r"\s*:\s*([^|]{1,240})\|([^|]{1,160})\|(.{1,4000})\s*$",
    re.I,
)
UNCERTAIN_CANDIDATE_RE = re.compile(
    r"(?:\?|\b(?:maybe|possibly|perhaps|probably|uncertain|unconfirmed|unknown|rumou?r(?:ed)?|"
    r"appears?|seems?|might|could|i\s+(?:think|believe|guess))\b)",
    re.I,
)
SENSITIVE_CANDIDATE_RE = re.compile(
    r"(?:\b(?:password|passcode|secret|credential|api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|"
    r"private[-_ ]?key|social security|ssn|credit card|bank account|routing number|date of birth)\b|"
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"\b(?:\+?\d[\d .()\-]{7,}\d)\b)",
    re.I,
)
MAX_SYNTHESIZED_CANDIDATES = 100
SAFE_REVIEW_REASONS = {
    "manual-review-required",
    "sensitive-fact-requires-review",
    "uncertain-inference-requires-review",
    "decision-requires-review",
    "preference-requires-review",
    "procedure-requires-review",
    "policy-requires-review",
    "instruction-requires-review",
    "episode-requires-review",
    "prompt-injection-requires-review",
    "conflict-requires-review",
    "correction-requires-review",
}
CHUNK_CHAR_LIMIT = 1200
CHUNK_CHAR_OVERLAP = 160
MAX_CHUNKS_PER_EXTRACTION = 2048
EMBEDDING_DIMENSIONS = 128
EMBEDDING_VERSION = "brain-hash-embedding-v1"
SEMANTIC_RETRIEVAL_THRESHOLD = 0.28
SEMANTIC_DUPLICATE_THRESHOLD = 0.90
SEMANTIC_KEY_THRESHOLD = 0.82
DEFAULT_TOPIC_RECEIPT = Path.home() / ".openclaw/private/telegram-topic-control/brain-topic-creation.json"
DEFAULT_AUTHORIZED_SENDER_RECEIPT = (
    Path.home() / ".openclaw/private/telegram-topic-control/brain-authorized-sender.json"
)
EXECUTABLE_SIGNATURES = (
    b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe", b"#!",
)
OOXML_TYPES = {
    "word/": "office-document",
    "xl/": "spreadsheet",
    "ppt/": "presentation",
}


class BrainIntakeError(RuntimeError):
    code = "brain-intake-error"


class BrainConfigurationError(BrainIntakeError):
    code = "brain-config-invalid"


class BrainSafetyError(BrainIntakeError):
    code = "brain-safety-rejected"


class BrainAuthorizationError(BrainIntakeError):
    code = "brain-action-unauthorized"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> dt.datetime:
    text = str(value or "")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def stable_id(prefix: str, *parts: Any, length: int = 24) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    return f"{prefix}-{hashlib.sha256(material.encode()).hexdigest()[:length]}"


def clean_text(value: Any, limit: int = 1200) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = CONTROL_CHARS.sub("", text)
    text = " ".join(text.split())
    return text[:limit]


def semantic_tokens(value: Any) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.findall(r"[a-z0-9][a-z0-9_.-]{1,63}", normalized)[:4096]


def grounded_objective(
    *,
    media_classes: Sequence[str],
    caption_present: bool,
    extracted_text: str = "",
) -> str:
    verified = sorted({
        clean_text(value, 40).lower()
        for value in media_classes
        if re.fullmatch(r"[a-z][a-z0-9-]{1,39}", clean_text(value, 40).lower())
    })
    media_label = verified[0] if len(verified) == 1 else "mixed media" if verified else "media"
    if caption_present:
        return clean_text(f"Govern a captioned verified {media_label} Brain submission", 240)
    text = clean_text(extracted_text, 4000)
    signal = "content pending extraction"
    if text:
        if PROMPT_INJECTION_RE.search(text) or SENSITIVE_CANDIDATE_RE.search(text):
            signal = "content requiring governed review"
        else:
            explicit = next(
                (match for line in text.splitlines() if (match := CANDIDATE_LINE_RE.fullmatch(line))),
                None,
            )
            if explicit:
                signal = clean_text(explicit.group(2), 96)
            else:
                tokens = semantic_tokens(text)[:8]
                signal = " ".join(tokens) if tokens else "content with no extractable summary"
    return clean_text(f"Govern verified {media_label} evidence about {signal}", 240)


def local_embedding(value: Any) -> tuple[list[float], float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for token in semantic_tokens(value):
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:2], "big") % EMBEDDING_DIMENSIONS
        vector[index] += 1.0 if digest[2] & 1 else -1.0
    norm = math.sqrt(sum(component * component for component in vector))
    if norm:
        vector = [round(component / norm, 8) for component in vector]
        norm = 1.0
    return vector, norm


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(float(a) * float(b) for a, b in zip(left, right)) / (left_norm * right_norm)


def bounded_chunks(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", str(text or ""))
    if not normalized:
        return []
    chunks: list[str] = []
    cursor = 0
    while cursor < len(normalized) and len(chunks) < MAX_CHUNKS_PER_EXTRACTION:
        end = min(len(normalized), cursor + CHUNK_CHAR_LIMIT)
        if end < len(normalized):
            split_at = max(normalized.rfind("\n", cursor + 1, end), normalized.rfind(" ", cursor + 1, end))
            if split_at > cursor + CHUNK_CHAR_LIMIT // 2:
                end = split_at
        chunk = normalized[cursor:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        cursor = max(cursor + 1, end - CHUNK_CHAR_OVERLAP)
    return chunks


def enforce_deadline(deadline_monotonic: float | None) -> None:
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise BrainIntakeError("worker-time-budget")


def brain_work_identity(chat_id: str, topic_id: str, source_key: str) -> tuple[str, str, str]:
    from telegram_gateway_lifecycle import canonical_work_id

    origin_key = stable_id("brain-origin", chat_id, topic_id, source_key, length=40)
    run_id = stable_id("brain-run", origin_key, LIFECYCLE_VERSION, length=32)
    return canonical_work_id(origin_key, run_id), origin_key, run_id


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BrainConfigurationError("invalid-json") from exc
    if not isinstance(value, dict):
        raise BrainConfigurationError("invalid-json-shape")
    return value


def load_private_receipt(path: Path, *, error_prefix: str) -> dict[str, Any]:
    raw_path = path.expanduser()
    if raw_path.is_symlink():
        raise BrainConfigurationError(f"{error_prefix}-symlink")
    try:
        receipt_info = raw_path.lstat()
    except OSError as exc:
        raise BrainConfigurationError(f"{error_prefix}-unreadable") from exc
    if (
        not stat.S_ISREG(receipt_info.st_mode)
        or receipt_info.st_uid != os.getuid()
        or receipt_info.st_nlink != 1
    ):
        raise BrainConfigurationError(f"{error_prefix}-owner-invalid")
    if stat.S_IMODE(receipt_info.st_mode) != 0o600:
        raise BrainConfigurationError(f"{error_prefix}-permissions-invalid")
    return load_json(raw_path)


def rollout_allows_brain(path: Path) -> bool:
    data = load_json(path)
    return bool(
        data.get("masterState") in {"josh2", "all"}
        and not data.get("globalKillSwitch", False)
        and not data.get("brainKillSwitch", True)
        and (data.get("hosts") or {}).get("josh2") is True
    )


def brain_ingestion_enabled(config: Mapping[str, Any], rollout_path: Path) -> bool:
    dynamic = config.get("dynamicTopics") or {}
    brain_topic = dynamic.get("brain") if isinstance(dynamic, dict) else None
    return bool(
        isinstance(brain_topic, dict)
        and brain_topic.get("enabled") is True
        and rollout_allows_brain(rollout_path)
    )


def private_brain_topic_receipt(receipt_path: Path) -> tuple[str, str]:
    receipt = load_private_receipt(receipt_path, error_prefix="topic-receipt")
    if receipt.get("state") != "confirmed" or receipt.get("topicName") != "Brain":
        raise BrainConfigurationError("topic-receipt-not-confirmed")
    try:
        attempt_count = int(receipt.get("attemptCount") or 0)
    except (TypeError, ValueError) as exc:
        raise BrainConfigurationError("topic-receipt-attempt-count-invalid") from exc
    if attempt_count != 1:
        raise BrainConfigurationError("topic-receipt-attempt-count-invalid")
    chat_id = clean_text(receipt.get("chatId"), 80)
    topic_id = clean_text(receipt.get("topicId"), 80)
    if not re.fullmatch(r"-?\d+", chat_id) or not re.fullmatch(r"\d+", topic_id):
        raise BrainConfigurationError("topic-receipt-identifiers-invalid")
    return chat_id, topic_id


def resolved_brain_topic(config: Mapping[str, Any], receipt_path: Path) -> tuple[str, str]:
    dynamic = config.get("dynamicTopics") or {}
    brain = dynamic.get("brain") if isinstance(dynamic, dict) else None
    if not isinstance(brain, dict):
        raise BrainConfigurationError("brain-dynamic-topic-missing")
    if (
        brain.get("label") != "Brain"
        or brain.get("owner") != "josh2"
        or brain.get("lane") != "brain-intake"
        or brain.get("topicIdSource") != "private-confirmed-receipt"
    ):
        raise BrainConfigurationError("brain-dynamic-topic-invalid")
    forbidden = {"groupId", "topicId", "receiptPath", "topicReceipt", "chatId"}
    if forbidden.intersection(brain):
        raise BrainConfigurationError("brain-dynamic-topic-private-data")
    return private_brain_topic_receipt(receipt_path)


def resolved_authorized_sender(
    receipt_path: Path,
    *,
    chat_id: str,
    topic_id: str,
) -> str:
    receipt = load_private_receipt(receipt_path, error_prefix="authorized-sender-receipt")
    if receipt.get("state") != "confirmed" or receipt.get("owner") != "josh2":
        raise BrainConfigurationError("authorized-sender-receipt-not-confirmed")
    authorized_sender = clean_text(receipt.get("authorizedSenderId"), 120)
    receipt_chat = clean_text(receipt.get("chatId"), 80)
    receipt_topic = clean_text(receipt.get("topicId"), 80)
    if (
        not re.fullmatch(r"\d+", authorized_sender)
        or not re.fullmatch(r"-?\d+", receipt_chat)
        or not re.fullmatch(r"\d+", receipt_topic)
    ):
        raise BrainConfigurationError("authorized-sender-receipt-identifiers-invalid")
    if not hmac.compare_digest(receipt_chat, chat_id) or not hmac.compare_digest(receipt_topic, topic_id):
        raise BrainConfigurationError("authorized-sender-receipt-topic-mismatch")
    return authorized_sender


def configure_brain_topic(config_path: Path, receipt_path: Path) -> dict[str, Any]:
    config = load_json(config_path)
    private_brain_topic_receipt(receipt_path)
    dynamic = config.setdefault("dynamicTopics", {}).setdefault("brain", {})
    dynamic.clear()
    dynamic.update({
        "label": "Brain", "owner": "josh2", "lane": "brain-intake",
        "topicIdSource": "private-confirmed-receipt", "enabled": False,
    })
    encoded = (json.dumps(config, indent=2, sort_keys=False) + "\n").encode()
    atomic_write(config_path, encoded, mode=0o644)
    # Verify the just-written source without returning either identifier.
    resolved_brain_topic(load_json(config_path), receipt_path)
    return {"ok": True, "configured": True, "unique": True, "owner": "josh2", "lane": "brain-intake"}


def detect_media_type(path: Path) -> tuple[str, str, list[str]]:
    with path.open("rb") as handle:
        head = handle.read(8192)
    warnings: list[str] = []
    if any(head.startswith(signature) for signature in EXECUTABLE_SIGNATURES):
        return "executable", "application/x-executable", ["executable-content"]
    if head.startswith(b"%PDF-"):
        return "document", "application/pdf", warnings
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png", warnings
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg", warnings
    if head[:6] in {b"GIF87a", b"GIF89a"}:
        return "image", "image/gif", warnings
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image", "image/webp", warnings
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio", "audio/wav", warnings
    if head.startswith(b"OggS"):
        return "audio", "audio/ogg", warnings
    if head.startswith(b"ID3") or head[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return "audio", "audio/mpeg", warnings
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        return ("audio", "audio/mp4", warnings) if brand in {b"M4A ", b"M4B "} else ("video", "video/mp4", warnings)
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
        except (zipfile.BadZipFile, OSError):
            return "archive", "application/zip", ["malformed-archive"]
        for prefix, media_class in OOXML_TYPES.items():
            if any(name.startswith(prefix) for name in names):
                mime = {
                    "word/": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "xl/": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "ppt/": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                }[prefix]
                return media_class, mime, warnings
        return "archive", "application/zip", warnings
    if head.startswith((b"{", b"[")):
        try:
            if path.stat().st_size > MAX_TEXT_BYTES:
                raise json.JSONDecodeError("bounded-json-limit", "", 0)
            with path.open("r", encoding="utf-8", errors="strict") as handle:
                json.loads(handle.read(MAX_TEXT_BYTES + 1))
            return "structured-data", "application/json", warnings
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            pass
    if b"\x00" not in head:
        guessed = mimetypes.guess_type(path.name)[0] or "text/plain"
        if guessed in {"text/csv", "application/xml", "text/xml"}:
            return "structured-data", guessed, warnings
        if not guessed.startswith("text/"):
            warnings.append("content-signature-overrode-extension")
            guessed = "text/plain"
        return "text", guessed, warnings
    return "generic-document", "application/octet-stream", ["unsupported-signature"]


def inspect_zip(path: Path) -> list[str]:
    warnings: list[str] = []
    total_uncompressed = 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise BrainSafetyError("archive-entry-limit")
        for info in infos:
            raw_name = info.filename
            normalized_name = raw_name.replace("\\", "/")
            name = PurePosixPath(normalized_name)
            if (
                "\\" in raw_name
                or name.is_absolute()
                or ".." in name.parts
                or re.match(r"^[A-Za-z]:", normalized_name)
            ):
                raise BrainSafetyError("archive-path-traversal")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise BrainSafetyError("archive-symlink")
            if info.flag_bits & 0x1:
                raise BrainSafetyError("encrypted-archive")
            total_uncompressed += int(info.file_size)
            if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED:
                raise BrainSafetyError("archive-expansion-limit")
            if info.file_size and (
                not info.compress_size
                or info.file_size / info.compress_size > MAX_ARCHIVE_RATIO
            ):
                raise BrainSafetyError("archive-ratio-limit")
            lower = info.filename.lower()
            if lower.endswith(("vbaproject.bin", ".exe", ".dll", ".dylib", ".so", ".app", ".scr", ".bat", ".cmd", ".ps1")):
                warnings.append("active-content-isolated")
            if lower.endswith((".rels", ".xml")) and info.file_size <= MAX_TEXT_BYTES:
                relationship_data = archive.read(info)
                if (
                    (
                        lower.endswith(".rels")
                        and re.search(br"TargetMode\s*=\s*['\"]External['\"]", relationship_data, re.I)
                    )
                    or b"<!DOCTYPE" in relationship_data.upper()
                    or b"<!ENTITY" in relationship_data.upper()
                ):
                    warnings.append("active-content-isolated")
    return sorted(set(warnings))


def inspect_pdf(path: Path) -> list[str]:
    active_tokens = (
        b"/javascript", b"/js", b"/openaction", b"/launch",
        b"/embeddedfile", b"/richmedia", b"/xfa", b"/aa",
    )
    warnings: list[str] = []
    overlap = b""
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            sample = (overlap + chunk).lower()
            if any(token in sample for token in active_tokens):
                warnings.append("active-content-isolated")
            if b"/encrypt" in sample:
                warnings.append("encrypted-document")
            overlap = sample[-64:]
    return sorted(set(warnings))


def ooxml_text(path: Path) -> str:
    chunks: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            lower = info.filename.lower()
            if not lower.endswith(".xml") or info.file_size > MAX_TEXT_BYTES:
                continue
            if not lower.startswith(("word/", "xl/", "ppt/")):
                continue
            raw = archive.read(info)
            try:
                root = ElementTree.fromstring(raw)
            except ElementTree.ParseError:
                continue
            chunks.extend(node.text for node in root.iter() if node.text)
            if sum(len(value) for value in chunks) >= MAX_EXTRACTED_CHARS:
                break
    return "\n".join(chunks)[:MAX_EXTRACTED_CHARS]


def local_tool_version(executable: str, version_args: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            [executable, *version_args],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "version-unavailable"
    rendered = clean_text(completed.stdout or completed.stderr, 120)
    return rendered or "version-unavailable"


def extract_local(
    path: Path,
    media_class: str,
    mime_type: str,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    text = ""
    method = "none"
    warnings: list[str] = []
    confidence = 0.0
    coverage = "none"
    model_route = "local-none"
    tool_version = "n/a"
    if media_class in {"text", "structured-data"} and path.stat().st_size <= MAX_TEXT_BYTES:
        text = path.read_text(errors="replace")[:MAX_EXTRACTED_CHARS]
        method, confidence, coverage = "bounded-text", 0.99, "full"
        model_route, tool_version = "local-deterministic", f"python-{sys.version_info.major}.{sys.version_info.minor}"
    elif media_class in {"office-document", "spreadsheet", "presentation"}:
        text = ooxml_text(path)
        method, confidence, coverage = "ooxml-xml", 0.92, "text-and-tables" if text else "none"
        model_route, tool_version = "local-deterministic", f"python-stdlib-{sys.version_info.major}.{sys.version_info.minor}"
        if not text:
            warnings.append("no-extractable-office-text")
    elif mime_type == "application/pdf":
        pdftotext = shutil.which("pdftotext")
        if not pdftotext:
            warnings.append("pdf-parser-unavailable")
        else:
            with tempfile.TemporaryDirectory(prefix="brain-pdf-") as temporary:
                out = Path(temporary) / "text.txt"
                try:
                    proc = subprocess.run(
                        [pdftotext, "-nopgbrk", "-layout", str(path), str(out)],
                        capture_output=True,
                        timeout=max(1.0, min(45.0, float(timeout_seconds or 45.0))),
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    warnings.append("pdf-parser-timeout")
                else:
                    if proc.returncode == 0 and out.exists():
                        text = out.read_text(errors="replace")[:MAX_EXTRACTED_CHARS]
                        method, confidence, coverage = "pdftotext-local", 0.9, "text-layer" if text else "none"
                        model_route = "local-tool"
                        tool_version = local_tool_version(pdftotext, ["-v"])
                    else:
                        warnings.append("pdf-parser-failed")
    elif media_class == "image":
        tesseract = shutil.which("tesseract")
        if not tesseract:
            warnings.append("ocr-unavailable")
        else:
            try:
                proc = subprocess.run(
                    [tesseract, str(path), "stdout"],
                    capture_output=True,
                    timeout=max(1.0, min(60.0, float(timeout_seconds or 60.0))),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                warnings.append("ocr-timeout")
            else:
                if proc.returncode == 0:
                    text = proc.stdout.decode(errors="replace")[:MAX_EXTRACTED_CHARS]
                    method, confidence, coverage = "tesseract-local", 0.82, "ocr" if text else "none"
                    model_route = "local-tool"
                    tool_version = local_tool_version(tesseract, ["--version"])
                else:
                    warnings.append("ocr-failed")
    elif media_class in {"audio", "video"}:
        warnings.append("local-transcription-unavailable")
    elif media_class == "archive":
        warnings.append("archive-indexed-without-execution")
    else:
        warnings.append("unsupported-extractor")
    injection = bool(PROMPT_INJECTION_RE.search(text) or UNSAFE_LINK_RE.search(text))
    if injection:
        warnings.append("untrusted-instruction-pattern")
    return {
        "text": text,
        "method": method,
        "confidence": confidence,
        "coverage": coverage,
        "warnings": sorted(set(warnings)),
        "promptInjection": injection,
        "supported": bool(text),
        "modelRoute": model_route,
        "toolVersion": tool_version,
    }


class BrainStore:
    def __init__(
        self,
        root: Path | str,
        *,
        download_roots: Sequence[Path | str] | None = None,
        authorized_sender_receipt: Path | str | None = None,
    ) -> None:
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise BrainConfigurationError("brain-store-root-symlink")
        self.root = raw_root.resolve()
        self.staging = self.root / "staging"
        self.cas = self.root / "cas"
        self.quarantine = self.root / "quarantine"
        self.extracted = self.root / "extracted"
        for directory in (self.root, self.staging, self.cas, self.quarantine, self.extracted):
            if directory.is_symlink():
                raise BrainConfigurationError("brain-store-directory-symlink")
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
        if download_roots is None:
            configured_roots = [
                value for value in os.environ.get("BRAIN_INTAKE_DOWNLOAD_ROOTS", "").split(os.pathsep)
                if value
            ]
            download_roots = configured_roots or [Path.home() / ".openclaw" / "media" / "inbound"]
        self.download_roots = tuple(
            Path(value).expanduser().resolve(strict=False) for value in download_roots
        )
        if not self.download_roots:
            raise BrainConfigurationError("download-root-allowlist-empty")
        configured_sender_receipt = (
            authorized_sender_receipt
            or os.environ.get("BRAIN_AUTHORIZED_SENDER_RECEIPT")
            or DEFAULT_AUTHORIZED_SENDER_RECEIPT
        )
        self.authorized_sender_receipt = Path(configured_sender_receipt).expanduser()
        self.db_path = self.root / "brain-intake.sqlite3"
        if self.db_path.is_symlink():
            raise BrainConfigurationError("brain-store-database-symlink")
        if not self.db_path.exists():
            descriptor = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        database_info = self.db_path.lstat()
        if (
            not stat.S_ISREG(database_info.st_mode)
            or database_info.st_uid != os.getuid()
            or database_info.st_nlink != 1
        ):
            raise BrainConfigurationError("brain-store-database-owner-invalid")
        os.chmod(self.db_path, 0o600)
        with self.connect() as db:
            self._schema(db)

    def _authorized_sender(self, *, chat_id: str, topic_id: str) -> str:
        return resolved_authorized_sender(
            self.authorized_sender_receipt,
            chat_id=chat_id,
            topic_id=topic_id,
        )

    def _authorize_actor(self, authorized_user: str, work_id: str = "") -> str:
        normalized_user = clean_text(authorized_user, 160)
        if work_id:
            with self.connect() as db:
                submission = db.execute(
                    "SELECT phase,source_private_json FROM submissions WHERE work_id=?", (work_id,),
                ).fetchone()
            if not submission:
                raise BrainIntakeError("unknown-work")
            if submission["phase"] == "forgotten":
                raise BrainIntakeError("source-already-forgotten")
            try:
                source = json.loads(str(submission["source_private_json"]))
            except json.JSONDecodeError as exc:
                raise BrainIntakeError("source-receipt-corrupt") from exc
            chat_id = clean_text(source.get("chatRef"), 100)
            topic_id = clean_text(source.get("topicRef"), 100)
        else:
            receipt = load_private_receipt(
                self.authorized_sender_receipt,
                error_prefix="authorized-sender-receipt",
            )
            chat_id = clean_text(receipt.get("chatId"), 100)
            topic_id = clean_text(receipt.get("topicId"), 100)
        expected = self._authorized_sender(chat_id=chat_id, topic_id=topic_id)
        if not normalized_user or not hmac.compare_digest(expected, normalized_user):
            raise BrainAuthorizationError("brain-owner-authorization-required")
        return normalized_user

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self.db_path.is_symlink():
            raise BrainConfigurationError("brain-store-database-symlink")
        database_info = self.db_path.lstat()
        if (
            not stat.S_ISREG(database_info.st_mode)
            or database_info.st_uid != os.getuid()
            or database_info.st_nlink != 1
        ):
            raise BrainConfigurationError("brain-store-database-owner-invalid")
        for sidecar in (
            self.db_path.with_name(self.db_path.name + "-wal"),
            self.db_path.with_name(self.db_path.name + "-shm"),
        ):
            try:
                sidecar_info = sidecar.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(sidecar_info.st_mode):
                raise BrainConfigurationError("brain-store-database-sidecar-symlink")
            if (
                not stat.S_ISREG(sidecar_info.st_mode)
                or sidecar_info.st_uid != os.getuid()
                or sidecar_info.st_nlink != 1
            ):
                raise BrainConfigurationError("brain-store-database-sidecar-owner-invalid")
        os.chmod(self.db_path, 0o600)
        db = sqlite3.connect(self.db_path, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=20000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        finally:
            db.close()
            with contextlib.suppress(FileNotFoundError):
                os.chmod(self.db_path, 0o600)
            for sidecar in (self.db_path.with_name(self.db_path.name + "-wal"), self.db_path.with_name(self.db_path.name + "-shm")):
                with contextlib.suppress(FileNotFoundError):
                    os.chmod(sidecar, 0o600)

    def _release_download_claim(self, work_id: str, attachment_id: str) -> None:
        with self.connect() as db, self.transaction(db):
            db.execute(
                """UPDATE attachment_intents SET state='receipt_pending'
                   WHERE id=? AND work_id=? AND state='downloading' AND consumed_at IS NULL""",
                (attachment_id, work_id),
            )
            db.execute(
                """UPDATE submissions SET phase='receipt_pending',updated_at=?
                   WHERE work_id=? AND phase='downloading'""",
                (utc_now(), work_id),
            )

    def _stage_download(self, source_path: Path) -> tuple[Path, os.stat_result, Path]:
        raw_source = source_path.expanduser()
        if not raw_source.is_absolute():
            raise BrainSafetyError("download-source-not-absolute")
        if raw_source.is_symlink():
            raise BrainSafetyError("download-source-symlink")
        try:
            resolved = raw_source.resolve(strict=True)
        except OSError as exc:
            raise BrainSafetyError("download-source-unreadable") from exc
        if not any(root == resolved or root in resolved.parents for root in self.download_roots):
            raise BrainSafetyError("download-source-outside-allowlist")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        source_fd = -1
        temporary_fd = -1
        temporary_name = ""
        try:
            source_fd = os.open(resolved, flags)
            info = os.fstat(source_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise BrainSafetyError("download-source-not-trusted")
            if info.st_size > MAX_FILE_BYTES:
                raise BrainSafetyError("file-size-limit")
            temporary_fd, temporary_name = tempfile.mkstemp(prefix="download-", dir=self.staging)
            os.fchmod(temporary_fd, 0o600)
            copied = 0
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > MAX_FILE_BYTES:
                    raise BrainSafetyError("file-size-limit")
                view = memoryview(chunk)
                while view:
                    written = os.write(temporary_fd, view)
                    view = view[written:]
            if copied != info.st_size:
                raise BrainSafetyError("download-source-changed-during-copy")
            os.fsync(temporary_fd)
            return Path(temporary_name), info, resolved
        except OSError as exc:
            raise BrainSafetyError("download-source-unreadable") from exc
        finally:
            if source_fd >= 0:
                os.close(source_fd)
            if temporary_fd >= 0:
                os.close(temporary_fd)
            if temporary_name and sys.exc_info()[0] is not None:
                Path(temporary_name).unlink(missing_ok=True)

    @staticmethod
    def _cleanup_fingerprint(info: os.stat_result) -> str:
        return json.dumps({
            "device": int(info.st_dev),
            "inode": int(info.st_ino),
            "size": int(info.st_size),
            "mtimeNs": int(info.st_mtime_ns),
        }, sort_keys=True, separators=(",", ":"))

    def _remove_gateway_download(
        self,
        source_path: Path,
        *,
        expected_fingerprint: str,
    ) -> None:
        """Unlink only the exact no-follow gateway file that was staged."""
        if not source_path.is_absolute() or source_path.is_symlink():
            raise BrainSafetyError("download-source-cleanup-invalid")
        if not any(root == source_path or root in source_path.parents for root in self.download_roots):
            raise BrainSafetyError("download-source-cleanup-invalid")
        try:
            expected = json.loads(expected_fingerprint)
        except (json.JSONDecodeError, TypeError) as exc:
            raise BrainSafetyError("download-source-cleanup-invalid") from exc
        parent_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            parent_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            parent_flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            parent_flags |= os.O_CLOEXEC
        parent_fd = -1
        try:
            parent_fd = os.open(source_path.parent, parent_flags)
            current = os.stat(source_path.name, dir_fd=parent_fd, follow_symlinks=False)
            actual = {
                "device": int(current.st_dev),
                "inode": int(current.st_ino),
                "size": int(current.st_size),
                "mtimeNs": int(current.st_mtime_ns),
            }
            if (
                actual != expected
                or not stat.S_ISREG(current.st_mode)
                or current.st_uid != os.getuid()
                or current.st_nlink != 1
            ):
                raise BrainSafetyError("download-source-cleanup-changed")
            os.unlink(source_path.name, dir_fd=parent_fd)
            os.fsync(parent_fd)
            try:
                os.stat(source_path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            raise BrainSafetyError("download-source-cleanup-incomplete")
        except BrainSafetyError:
            raise
        except OSError as exc:
            raise BrainSafetyError("download-source-cleanup-failed") from exc
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)

    def _verified_private_artifact(self, path: Path, digest: str) -> Path:
        if path.is_symlink():
            raise BrainSafetyError("private-artifact-path-invalid")
        try:
            resolved = path.resolve(strict=True)
            info = resolved.lstat()
        except OSError as exc:
            raise BrainSafetyError("private-artifact-path-invalid") from exc
        if (
            self.root not in resolved.parents
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or sha256_file(resolved) != digest
        ):
            raise BrainSafetyError("private-artifact-path-invalid")
        return resolved

    @contextlib.contextmanager
    def transaction(self, db: sqlite3.Connection) -> Iterator[None]:
        db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            db.rollback()
            raise
        else:
            db.commit()

    def _schema(self, db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS submissions (
              work_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
              lifecycle_version INTEGER NOT NULL, extraction_version TEXT NOT NULL,
              source_revision INTEGER NOT NULL, intake_agent TEXT NOT NULL,
              current_owner TEXT NOT NULL, phase TEXT NOT NULL,
              privacy_class TEXT NOT NULL, caption_present INTEGER NOT NULL,
              caption_private TEXT NOT NULL, objective_private TEXT NOT NULL,
              media_group_ref TEXT NOT NULL, source_private_json TEXT NOT NULL,
              reference_only INTEGER NOT NULL DEFAULT 0,
              cancel_requested INTEGER NOT NULL DEFAULT 0,
              user_cancel_requested INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, forgotten_at TEXT
            );
            CREATE TABLE IF NOT EXISTS attachment_intents (
              id TEXT PRIMARY KEY, work_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
              source_message_ref TEXT NOT NULL, file_ref TEXT NOT NULL,
              media_kind TEXT NOT NULL, declared_mime TEXT NOT NULL,
              declared_size INTEGER NOT NULL, token_hash TEXT NOT NULL UNIQUE,
              state TEXT NOT NULL, consumed_at TEXT,
              failure_reason TEXT NOT NULL DEFAULT '',
              source_cleanup_state TEXT NOT NULL DEFAULT 'n/a',
              source_cleanup_path TEXT NOT NULL DEFAULT '',
              source_cleanup_fingerprint TEXT NOT NULL DEFAULT '',
              UNIQUE(work_id, ordinal), FOREIGN KEY(work_id) REFERENCES submissions(work_id)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
              digest TEXT PRIMARY KEY, stored_path TEXT NOT NULL,
              media_class TEXT NOT NULL, detected_mime TEXT NOT NULL,
              size_bytes INTEGER NOT NULL, ref_count INTEGER NOT NULL,
              quarantine_reason TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS submission_artifacts (
              work_id TEXT NOT NULL, attachment_id TEXT NOT NULL, digest TEXT NOT NULL,
              PRIMARY KEY(work_id, attachment_id),
              FOREIGN KEY(work_id) REFERENCES submissions(work_id),
              FOREIGN KEY(digest) REFERENCES artifacts(digest)
            );
            CREATE TABLE IF NOT EXISTS extractions (
              id TEXT PRIMARY KEY, work_id TEXT NOT NULL, digest TEXT NOT NULL,
              version TEXT NOT NULL, method TEXT NOT NULL, status TEXT NOT NULL,
              private_path TEXT NOT NULL, text_hash TEXT NOT NULL,
              confidence REAL NOT NULL, coverage TEXT NOT NULL,
              warnings_json TEXT NOT NULL, prompt_injection INTEGER NOT NULL,
              created_at TEXT NOT NULL, model_route TEXT NOT NULL DEFAULT 'local-none',
              tool_version TEXT NOT NULL DEFAULT 'n/a',
              FOREIGN KEY(work_id) REFERENCES submissions(work_id)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
              extraction_id UNINDEXED, work_id UNINDEXED, evidence_text,
              tokenize='porter unicode61'
            );
            CREATE TABLE IF NOT EXISTS source_chunks (
              id TEXT PRIMARY KEY, extraction_id TEXT NOT NULL, work_id TEXT NOT NULL,
              ordinal INTEGER NOT NULL, text_private TEXT NOT NULL,
              text_hash TEXT NOT NULL, token_count INTEGER NOT NULL,
              confidence REAL NOT NULL, coverage TEXT NOT NULL,
              provenance_ref TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(extraction_id,ordinal),
              FOREIGN KEY(extraction_id) REFERENCES extractions(id),
              FOREIGN KEY(work_id) REFERENCES submissions(work_id)
            );
            CREATE TABLE IF NOT EXISTS source_vectors (
              chunk_id TEXT PRIMARY KEY, embedding_version TEXT NOT NULL,
              dimensions INTEGER NOT NULL, vector_json TEXT NOT NULL,
              norm REAL NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(chunk_id) REFERENCES source_chunks(id)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS source_chunk_fts USING fts5(
              chunk_id UNINDEXED, work_id UNINDEXED, chunk_text,
              tokenize='porter unicode61'
            );
            CREATE TABLE IF NOT EXISTS candidates (
              id TEXT PRIMARY KEY, work_id TEXT NOT NULL, candidate_type TEXT NOT NULL,
              subject TEXT NOT NULL, predicate TEXT NOT NULL, value_private TEXT NOT NULL,
              privacy_class TEXT NOT NULL, confidence REAL NOT NULL,
              provenance_ref TEXT NOT NULL, status TEXT NOT NULL,
              eligibility_reason TEXT NOT NULL, registry_candidate_id TEXT NOT NULL,
              registry_memory_id TEXT NOT NULL, created_at TEXT NOT NULL,
              duplicate_of TEXT NOT NULL DEFAULT '', conflicts_with TEXT NOT NULL DEFAULT '',
              semantic_score REAL NOT NULL DEFAULT 0,
              FOREIGN KEY(work_id) REFERENCES submissions(work_id)
            );
            CREATE TABLE IF NOT EXISTS source_revision_events (
              id TEXT PRIMARY KEY, work_id TEXT NOT NULL, source_revision INTEGER NOT NULL,
              event_kind TEXT NOT NULL, source_hash TEXT NOT NULL,
              caption_private TEXT NOT NULL, source_private_json TEXT NOT NULL,
              attachments_private_json TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(work_id,source_revision), UNIQUE(work_id,source_hash),
              FOREIGN KEY(work_id) REFERENCES submissions(work_id)
            );
            CREATE TABLE IF NOT EXISTS actions (
              token_hash TEXT PRIMARY KEY, work_id TEXT NOT NULL,
              authorized_user TEXT NOT NULL, action TEXT NOT NULL,
              impact_json TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT,
              created_at TEXT NOT NULL, FOREIGN KEY(work_id) REFERENCES submissions(work_id)
            );
            CREATE TABLE IF NOT EXISTS deletion_receipts (
              id TEXT PRIMARY KEY, work_id_hash TEXT NOT NULL, artifact_count INTEGER NOT NULL,
              extraction_count INTEGER NOT NULL, candidate_count INTEGER NOT NULL,
              memory_count INTEGER NOT NULL, blob_deleted_count INTEGER NOT NULL,
              retrieval_hits_after INTEGER NOT NULL, completed_at TEXT NOT NULL
            );
            -- Migration compatibility only.  These legacy caller-recorded rows
            -- are never release evidence; signed brain_fixture_suite
            -- attestations are authoritative.
            CREATE TABLE IF NOT EXISTS fixture_runs (
              id TEXT PRIMARY KEY, media_class TEXT NOT NULL, outcome TEXT NOT NULL,
              privacy_ok INTEGER NOT NULL, cleanup_ok INTEGER NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lifecycle_bindings (
              work_id TEXT PRIMARY KEY, lifecycle_work_id TEXT NOT NULL UNIQUE,
              lifecycle_run_id TEXT NOT NULL, source_revision_at_start INTEGER NOT NULL,
              writer_authority_at_start INTEGER NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(work_id) REFERENCES submissions(work_id)
            );
            CREATE TABLE IF NOT EXISTS intake_jobs (
              work_id TEXT PRIMARY KEY, state TEXT NOT NULL, stage TEXT NOT NULL,
              fairness_lane TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
              max_attempts INTEGER NOT NULL, available_at TEXT NOT NULL,
              lease_owner TEXT NOT NULL DEFAULT '', lease_expires_at TEXT,
              error_class TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL, completed_at TEXT,
              FOREIGN KEY(work_id) REFERENCES submissions(work_id)
            );
            CREATE INDEX IF NOT EXISTS idx_intake_jobs_ready
              ON intake_jobs(state,available_at,attempt_count,created_at);
            CREATE TABLE IF NOT EXISTS intake_results (
              result_id TEXT PRIMARY KEY, work_id TEXT NOT NULL UNIQUE,
              lifecycle_work_id TEXT NOT NULL, terminal_event_id TEXT NOT NULL,
              outcome TEXT NOT NULL, payload_hash TEXT NOT NULL,
              private_payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(work_id) REFERENCES submissions(work_id)
            );
            CREATE TABLE IF NOT EXISTS intake_terminal_prepares (
              work_id TEXT PRIMARY KEY, outcome TEXT NOT NULL,
              payload_hash TEXT NOT NULL, private_payload_json TEXT NOT NULL,
              attempt_fence INTEGER NOT NULL, lease_owner_hash TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(work_id) REFERENCES submissions(work_id)
            );
            CREATE TABLE IF NOT EXISTS intake_worker_meta (
              key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        prepare_columns = {
            str(row["name"]) for row in db.execute("PRAGMA table_info(intake_terminal_prepares)")
        }
        if "attempt_fence" not in prepare_columns:
            db.execute(
                "ALTER TABLE intake_terminal_prepares ADD COLUMN attempt_fence INTEGER NOT NULL DEFAULT 0"
            )
        if "lease_owner_hash" not in prepare_columns:
            db.execute(
                "ALTER TABLE intake_terminal_prepares ADD COLUMN lease_owner_hash TEXT NOT NULL DEFAULT ''"
            )
        extraction_columns = {
            str(row["name"]) for row in db.execute("PRAGMA table_info(extractions)")
        }
        if "model_route" not in extraction_columns:
            db.execute(
                "ALTER TABLE extractions ADD COLUMN model_route TEXT NOT NULL DEFAULT 'local-none'"
            )
        if "tool_version" not in extraction_columns:
            db.execute(
                "ALTER TABLE extractions ADD COLUMN tool_version TEXT NOT NULL DEFAULT 'n/a'"
            )
        attachment_columns = {
            str(row["name"]) for row in db.execute("PRAGMA table_info(attachment_intents)")
        }
        if "failure_reason" not in attachment_columns:
            db.execute(
                "ALTER TABLE attachment_intents ADD COLUMN failure_reason TEXT NOT NULL DEFAULT ''"
            )
        if "source_cleanup_state" not in attachment_columns:
            db.execute(
                "ALTER TABLE attachment_intents ADD COLUMN source_cleanup_state TEXT NOT NULL DEFAULT 'n/a'"
            )
        if "source_cleanup_path" not in attachment_columns:
            db.execute(
                "ALTER TABLE attachment_intents ADD COLUMN source_cleanup_path TEXT NOT NULL DEFAULT ''"
            )
        if "source_cleanup_fingerprint" not in attachment_columns:
            db.execute(
                "ALTER TABLE attachment_intents ADD COLUMN source_cleanup_fingerprint TEXT NOT NULL DEFAULT ''"
            )
        submission_columns = {
            str(row["name"]) for row in db.execute("PRAGMA table_info(submissions)")
        }
        if "user_cancel_requested" not in submission_columns:
            db.execute(
                "ALTER TABLE submissions ADD COLUMN user_cancel_requested INTEGER NOT NULL DEFAULT 0"
            )
        candidate_columns = {
            str(row["name"]) for row in db.execute("PRAGMA table_info(candidates)")
        }
        if "duplicate_of" not in candidate_columns:
            db.execute("ALTER TABLE candidates ADD COLUMN duplicate_of TEXT NOT NULL DEFAULT ''")
        if "conflicts_with" not in candidate_columns:
            db.execute("ALTER TABLE candidates ADD COLUMN conflicts_with TEXT NOT NULL DEFAULT ''")
        if "semantic_score" not in candidate_columns:
            db.execute("ALTER TABLE candidates ADD COLUMN semantic_score REAL NOT NULL DEFAULT 0")

    def _fsync_receipt(self, db: sqlite3.Connection) -> None:
        db.execute("PRAGMA wal_checkpoint(FULL)")
        if self.db_path.exists():
            fd = os.open(self.db_path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        root_fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)

    def bind_lifecycle(self, work_id: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Persist the opaque Brain-to-Gateway identity before media download."""
        lifecycle_work_id = clean_text(receipt.get("workId"), 80)
        lifecycle_run_id = clean_text(receipt.get("runId"), 160)
        if (
            not re.fullmatch(r"work-telegram-[0-9a-f]{24}", lifecycle_work_id)
            or not lifecycle_run_id
            or receipt.get("surfaceContract") != "brain-intake"
            or receipt.get("currentOwner") != "josh2"
            or int(receipt.get("deliveryTier") or 0) != 3
            or not bool(receipt.get("writerAuthorityAtStart"))
            or not hmac.compare_digest(lifecycle_work_id, work_id)
        ):
            raise BrainConfigurationError("brain-lifecycle-receipt-invalid")
        with self.connect() as db, self.transaction(db):
            submission = db.execute(
                "SELECT source_revision FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()
            if not submission:
                raise BrainIntakeError("unknown-work")
            existing = db.execute(
                "SELECT * FROM lifecycle_bindings WHERE work_id=?", (work_id,),
            ).fetchone()
            expected = (
                lifecycle_work_id, lifecycle_run_id,
                int(submission["source_revision"]), 1,
            )
            if existing:
                actual = (
                    str(existing["lifecycle_work_id"]), str(existing["lifecycle_run_id"]),
                    int(existing["source_revision_at_start"]),
                    int(existing["writer_authority_at_start"]),
                )
                if actual[:2] != expected[:2] or actual[3] != 1:
                    raise BrainConfigurationError("brain-lifecycle-binding-conflict")
                return {"ok": True, "duplicate": True, "bound": True}
            db.execute(
                "INSERT INTO lifecycle_bindings VALUES(?,?,?,?,?,?)",
                (work_id, *expected, utc_now()),
            )
        return {"ok": True, "duplicate": False, "bound": True}

    def lifecycle_binding(self, work_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM lifecycle_bindings WHERE work_id=?", (work_id,),
            ).fetchone()
        return dict(row) if row else None

    def _refresh_private_objective(self, work_id: str, *, extracted_text: str = "") -> None:
        with self.connect() as db, self.transaction(db):
            submission = db.execute(
                "SELECT caption_present,phase FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()
            if not submission or submission["phase"] == "forgotten":
                return
            media_classes = [
                str(row["media_class"])
                for row in db.execute(
                    """SELECT DISTINCT a.media_class FROM submission_artifacts sa
                         JOIN artifacts a ON a.digest=sa.digest WHERE sa.work_id=?""",
                    (work_id,),
                ).fetchall()
            ]
            objective = grounded_objective(
                media_classes=media_classes,
                caption_present=bool(submission["caption_present"]),
                extracted_text=extracted_text,
            )
            db.execute(
                "UPDATE submissions SET objective_private=?,updated_at=? WHERE work_id=?",
                (objective, utc_now(), work_id),
            )

    def _enqueue_ready(
        self,
        db: sqlite3.Connection,
        work_id: str,
        *,
        force_cancel: bool = False,
    ) -> tuple[bool, bool]:
        """Enqueue once, inside the same transaction that makes storage ready."""
        submission = db.execute(
            "SELECT phase,cancel_requested,user_cancel_requested FROM submissions WHERE work_id=?", (work_id,),
        ).fetchone()
        if not submission:
            raise BrainIntakeError("unknown-work")
        remaining = int(db.execute(
            "SELECT COUNT(*) FROM attachment_intents WHERE work_id=? AND consumed_at IS NULL",
            (work_id,),
        ).fetchone()[0])
        cleanup_pending = int(db.execute(
            """SELECT COUNT(*) FROM attachment_intents
                 WHERE work_id=? AND source_cleanup_state='pending'""",
            (work_id,),
        ).fetchone()[0])
        cancelled = (
            bool(submission["cancel_requested"])
            or bool(submission["user_cancel_requested"])
            or submission["phase"] == "forgotten"
        )
        if cleanup_pending:
            return False, False
        if remaining and not (force_cancel or cancelled):
            return False, False
        lanes = sorted({
            str(row["media_class"])
            for row in db.execute(
                """SELECT a.media_class FROM submission_artifacts sa
                     JOIN artifacts a ON a.digest=sa.digest WHERE sa.work_id=?""",
                (work_id,),
            )
        })
        lane = (
            "cancelled"
            if (force_cancel or cancelled)
            else lanes[0]
            if len(lanes) == 1
            else "mixed"
            if lanes
            else "unsupported"
        )
        now = utc_now()
        created = db.execute(
            """INSERT OR IGNORE INTO intake_jobs(
                 work_id,state,stage,fairness_lane,attempt_count,max_attempts,
                 available_at,lease_owner,lease_expires_at,error_class,
                 created_at,updated_at,completed_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                work_id, "queued", "stored", clean_text(lane, 80), 0,
                WORKER_MAX_ATTEMPTS, now, "", None, "", now, now, None,
            ),
        ).rowcount == 1
        exists = bool(db.execute(
            "SELECT 1 FROM intake_jobs WHERE work_id=?", (work_id,),
        ).fetchone())
        return exists, created

    def begin_submission(
        self,
        envelope: Mapping[str, Any],
        *,
        privacy: str = "private",
        side_effects_started: bool = False,
    ) -> dict[str, Any]:
        if privacy not in PRIVACY_CLASSES:
            privacy = "private"
        chat_ref = clean_text(envelope.get("chatId"), 100)
        topic_ref = clean_text(envelope.get("threadId"), 100)
        message_ref = clean_text(envelope.get("messageId"), 100)
        media_group = clean_text(envelope.get("mediaGroupId"), 160)
        sender_ref = clean_text(envelope.get("senderId"), 120)
        if not chat_ref or not topic_ref or not message_ref or not sender_ref:
            raise BrainIntakeError("source-binding-incomplete")
        if envelope.get("senderIsBot") is not False:
            raise BrainAuthorizationError("brain-bot-origin-rejected")
        authorized_sender = self._authorized_sender(chat_id=chat_ref, topic_id=topic_ref)
        if not hmac.compare_digest(authorized_sender, sender_ref):
            raise BrainAuthorizationError("brain-owner-authorization-required")
        source_key = media_group or message_ref
        work_id, _, _ = brain_work_identity(chat_ref, topic_ref, source_key)
        caption = clean_text(envelope.get("caption"), 20_000)
        attachments = envelope.get("attachments") or []
        if not isinstance(attachments, list) or not attachments:
            raise BrainIntakeError("media-attachment-required")
        if len(attachments) > MAX_ATTACHMENTS_PER_SUBMISSION:
            raise BrainIntakeError("attachment-count-limit")
        normalized_attachments: list[dict[str, Any]] = []
        declared_total = 0
        for ordinal, raw_attachment in enumerate(attachments):
            failure_reason = ""
            if isinstance(raw_attachment, dict):
                attachment = raw_attachment
            else:
                # The trusted gateway still has a durable Telegram message
                # binding for this album part.  Preserve a metadata-only
                # receipt instead of abandoning the whole lifecycle.
                attachment = {}
                failure_reason = "corrupt"
            source_message = clean_text(attachment.get("sourceMessageId") or message_ref, 100)
            file_ref = clean_text(attachment.get("fileId"), 300)
            kind = clean_text(attachment.get("kind"), 80) or "generic-document"
            try:
                declared_size = int(attachment.get("size") or 0)
            except (TypeError, ValueError):
                declared_size = 0
                failure_reason = "corrupt"
            if not source_message or not file_ref:
                failure_reason = "corrupt"
            if declared_size < 0:
                declared_size = 0
                failure_reason = "corrupt"
            elif declared_size > MAX_FILE_BYTES:
                failure_reason = "oversize"
            elif not failure_reason and declared_total + declared_size > MAX_SUBMISSION_BYTES:
                failure_reason = "oversize"
            if not failure_reason:
                declared_total += declared_size
            normalized_attachments.append({
                "ordinal": ordinal,
                "sourceMessageId": source_message,
                "fileId": file_ref,
                "kind": kind,
                "mime": clean_text(attachment.get("mime"), 120),
                "size": declared_size,
                "failureReason": failure_reason,
            })
        now = utc_now()
        objective = (
            "Process a captioned Brain media submission"
            if caption else "Extract and govern a Brain media submission"
        )
        source_private = {
            "chatRef": chat_ref,
            "topicRef": topic_ref,
            "messageRef": message_ref,
            "senderRef": sender_ref,
            "mediaGroupRef": media_group,
        }
        source_snapshot = json.dumps(
            {"source": source_private, "caption": caption, "attachments": normalized_attachments},
            sort_keys=True,
            separators=(",", ":"),
        )
        source_hash = hashlib.sha256(source_snapshot.encode()).hexdigest()
        tokens: list[dict[str, Any]] = []
        duplicate = edited = correction_pending = False
        phase = "receipt_pending"
        source_revision = 1

        def insert_intents(db: sqlite3.Connection) -> None:
            for attachment in normalized_attachments:
                token = secrets.token_urlsafe(24)
                attachment_id = stable_id(
                    "brain-attachment", work_id,
                    attachment["ordinal"], attachment["sourceMessageId"],
                )
                db.execute(
                    """INSERT INTO attachment_intents(
                         id,work_id,ordinal,source_message_ref,file_ref,media_kind,
                         declared_mime,declared_size,token_hash,state,consumed_at,failure_reason
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        attachment_id, work_id, attachment["ordinal"], attachment["sourceMessageId"],
                        attachment["fileId"], attachment["kind"], attachment["mime"], attachment["size"],
                        hashlib.sha256(token.encode()).hexdigest(),
                        "failure_pending" if attachment["failureReason"] else "receipt_pending",
                        None, attachment["failureReason"],
                    ),
                )
                tokens.append({
                    "attachmentId": attachment_id,
                    "sourceMessageId": attachment["sourceMessageId"],
                    "token": token,
                    "consumed": False,
                    "failureReason": attachment["failureReason"] or None,
                })

        with self.connect() as db, self.transaction(db):
            existing = db.execute("SELECT * FROM submissions WHERE work_id=?", (work_id,)).fetchone()
            if not existing:
                db.execute(
                    """INSERT INTO submissions(
                         work_id,schema_version,lifecycle_version,extraction_version,source_revision,
                         intake_agent,current_owner,phase,privacy_class,caption_present,
                         caption_private,objective_private,media_group_ref,source_private_json,
                         reference_only,cancel_requested,created_at,updated_at,forgotten_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        work_id, SCHEMA_VERSION, LIFECYCLE_VERSION, EXTRACTION_VERSION, 1,
                        "josh2", "josh2", "receipt_pending", privacy, int(bool(caption)),
                        caption, objective, media_group, json.dumps(source_private, sort_keys=True),
                        0, 0, now, now, None,
                    ),
                )
                insert_intents(db)
                db.execute(
                    "INSERT INTO source_revision_events VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        stable_id("brain-source-revision", work_id, 1, source_hash, length=28),
                        work_id, 1, "source-received", source_hash, caption,
                        json.dumps(source_private, sort_keys=True),
                        json.dumps(normalized_attachments, sort_keys=True), now,
                    ),
                )
            else:
                if existing["phase"] == "forgotten":
                    raise BrainIntakeError("source-already-forgotten")
                if existing["phase"] == "quarantined":
                    raise BrainSafetyError("source-quarantined")
                if existing["cancel_requested"] or existing["user_cancel_requested"]:
                    raise BrainIntakeError("source-forget-in-progress")
                latest = db.execute(
                    """SELECT * FROM source_revision_events WHERE work_id=?
                         ORDER BY source_revision DESC LIMIT 1""",
                    (work_id,),
                ).fetchone()
                if not latest:
                    raise BrainIntakeError("source-revision-history-missing")
                source_revision = int(existing["source_revision"])
                if hmac.compare_digest(str(latest["source_hash"]), source_hash):
                    duplicate = True
                    correction_pending = str(latest["event_kind"]) == "correction-requested"
                else:
                    local_effects = bool(
                        side_effects_started
                        or existing["phase"] != "receipt_pending"
                        or db.execute(
                            """SELECT 1 FROM attachment_intents WHERE work_id=?
                                 AND (consumed_at IS NOT NULL OR state NOT IN ('receipt_pending','failure_pending')) LIMIT 1""",
                            (work_id,),
                        ).fetchone()
                        or db.execute("SELECT 1 FROM submission_artifacts WHERE work_id=? LIMIT 1", (work_id,)).fetchone()
                        or db.execute("SELECT 1 FROM extractions WHERE work_id=? LIMIT 1", (work_id,)).fetchone()
                        or db.execute("SELECT 1 FROM candidates WHERE work_id=? LIMIT 1", (work_id,)).fetchone()
                        or db.execute("SELECT 1 FROM intake_jobs WHERE work_id=? LIMIT 1", (work_id,)).fetchone()
                    )
                    source_revision += 1
                    event_kind = "correction-requested" if local_effects else "source-revised"
                    db.execute(
                        "INSERT INTO source_revision_events VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            stable_id(
                                "brain-source-revision", work_id, source_revision, source_hash, length=28,
                            ),
                            work_id, source_revision, event_kind, source_hash, caption,
                            json.dumps(source_private, sort_keys=True),
                            json.dumps(normalized_attachments, sort_keys=True), now,
                        ),
                    )
                    if local_effects:
                        correction_pending = True
                        db.execute(
                            "UPDATE submissions SET source_revision=?,updated_at=? WHERE work_id=?",
                            (source_revision, now, work_id),
                        )
                    else:
                        edited = True
                        db.execute("DELETE FROM attachment_intents WHERE work_id=?", (work_id,))
                        db.execute(
                            """UPDATE submissions SET source_revision=?,phase='receipt_pending',
                                 caption_present=?,caption_private=?,objective_private=?,media_group_ref=?,
                                 source_private_json=?,updated_at=? WHERE work_id=?""",
                            (
                                source_revision, int(bool(caption)), caption, objective, media_group,
                                json.dumps(source_private, sort_keys=True), now, work_id,
                            ),
                        )
                        insert_intents(db)
                if not correction_pending and not edited:
                    stale_claim = (
                        existing["phase"] == "downloading"
                        and parse_utc(existing["updated_at"])
                        <= dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=DOWNLOAD_CLAIM_TTL_SECONDS)
                    )
                    if stale_claim:
                        db.execute(
                            """UPDATE attachment_intents SET state='receipt_pending'
                               WHERE work_id=? AND state='downloading' AND consumed_at IS NULL""",
                            (work_id,),
                        )
                        db.execute(
                            "UPDATE submissions SET phase='receipt_pending',updated_at=? WHERE work_id=?",
                            (now, work_id),
                        )
                    intents = db.execute(
                        "SELECT * FROM attachment_intents WHERE work_id=? ORDER BY ordinal", (work_id,),
                    ).fetchall()
                    for intent in intents:
                        base = {
                            "attachmentId": intent["id"],
                            "sourceMessageId": intent["source_message_ref"],
                            "failureReason": str(intent["failure_reason"] or "") or None,
                        }
                        if intent["consumed_at"]:
                            tokens.append({**base, "consumed": True})
                            continue
                        if intent["state"] not in {"receipt_pending", "failure_pending"}:
                            raise BrainAuthorizationError("download-resume-claim-active")
                        token = secrets.token_urlsafe(24)
                        db.execute(
                            "UPDATE attachment_intents SET token_hash=? WHERE id=? AND consumed_at IS NULL",
                            (hashlib.sha256(token.encode()).hexdigest(), intent["id"]),
                        )
                        tokens.append({**base, "token": token, "consumed": False})
                    phase = (
                        "receipt_pending"
                        if any(not item["consumed"] for item in tokens)
                        else str(existing["phase"])
                    )
                elif edited:
                    phase = "receipt_pending"
                else:
                    phase = str(existing["phase"])
        with self.connect() as db:
            self._fsync_receipt(db)
            queued = bool(db.execute(
                "SELECT 1 FROM intake_jobs WHERE work_id=?", (work_id,),
            ).fetchone()) or correction_pending
        return {
            "ok": True,
            "brain": True,
            "duplicate": duplicate,
            "edited": edited,
            "correctionPending": correction_pending,
            "resumed": duplicate and any(not item["consumed"] for item in tokens),
            "workId": work_id,
            "sourceRevision": source_revision,
            "phase": phase,
            "receiptPersisted": True,
            "attachmentCount": len(normalized_attachments),
            "downloadTokens": tokens,
            "queued": queued,
        }

    def fail_attachment(
        self,
        *,
        work_id: str,
        attachment_id: str,
        token: str,
        reason: str,
    ) -> dict[str, Any]:
        """Consume one capability into a fixed, metadata-only failure receipt.

        This boundary is intentionally lifecycle-gated.  It is used for
        declared oversize/corrupt metadata and for a trusted gateway download
        that could not produce a safe local file.  No exception text, filename,
        file identifier, or source path is retained as the reason.
        """
        safe_reason = clean_text(reason, 40)
        if safe_reason not in ATTACHMENT_FAILURE_REASONS:
            raise BrainIntakeError("attachment-failure-reason-invalid")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db, self.transaction(db):
            intent = db.execute(
                "SELECT * FROM attachment_intents WHERE id=? AND work_id=?",
                (attachment_id, work_id),
            ).fetchone()
            if not intent or not hmac.compare_digest(str(intent["token_hash"]), token_hash):
                raise BrainAuthorizationError("download-capability-invalid")
            if not db.execute(
                "SELECT 1 FROM lifecycle_bindings WHERE work_id=?", (work_id,),
            ).fetchone():
                raise BrainAuthorizationError("attachment-failure-lifecycle-unbound")
            declared_reason = str(intent["failure_reason"] or "")
            if declared_reason and not hmac.compare_digest(declared_reason, safe_reason):
                raise BrainAuthorizationError("attachment-failure-reason-mismatch")
            if intent["consumed_at"]:
                current = db.execute(
                    "SELECT phase FROM submissions WHERE work_id=?", (work_id,),
                ).fetchone()
                queued, _ = self._enqueue_ready(db, work_id)
                return {
                    "ok": True,
                    "duplicate": True,
                    "failedAttachment": bool(intent["failure_reason"]),
                    "unsupported": intent["state"] == "unsupported",
                    "quarantined": intent["state"] == "quarantined",
                    "errorClass": str(intent["failure_reason"] or "") or None,
                    "phase": str(current["phase"]) if current else "unsupported",
                    "queued": queued,
                    "queueCreated": False,
                }
            submission = db.execute(
                "SELECT phase,cancel_requested,user_cancel_requested FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()
            if (
                not submission
                or submission["phase"] in {"forgotten", "quarantined"}
                or submission["cancel_requested"]
                or submission["user_cancel_requested"]
            ):
                raise BrainAuthorizationError("download-work-not-active")
            state = ATTACHMENT_FAILURE_REASONS[safe_reason]
            consumed = db.execute(
                """UPDATE attachment_intents SET state=?,consumed_at=?,failure_reason=?
                     WHERE id=? AND work_id=? AND state IN ('receipt_pending','failure_pending')
                       AND consumed_at IS NULL""",
                (state, utc_now(), safe_reason, attachment_id, work_id),
            ).rowcount
            if consumed != 1:
                raise BrainAuthorizationError("download-capability-in-use")
            remaining = int(db.execute(
                "SELECT COUNT(*) FROM attachment_intents WHERE work_id=? AND consumed_at IS NULL",
                (work_id,),
            ).fetchone()[0])
            artifact_count = int(db.execute(
                "SELECT COUNT(*) FROM submission_artifacts WHERE work_id=?", (work_id,),
            ).fetchone()[0])
            artifact_quarantine = bool(db.execute(
                """SELECT 1 FROM submission_artifacts sa JOIN artifacts a ON a.digest=sa.digest
                     WHERE sa.work_id=? AND a.quarantine_reason!='' LIMIT 1""",
                (work_id,),
            ).fetchone())
            corrupt_failure = bool(db.execute(
                """SELECT 1 FROM attachment_intents WHERE work_id=?
                     AND consumed_at IS NOT NULL AND failure_reason='corrupt' LIMIT 1""",
                (work_id,),
            ).fetchone())
            phase = (
                "downloading"
                if remaining
                else "quarantined"
                if artifact_quarantine or (corrupt_failure and artifact_count == 0)
                else "stored"
                if artifact_count
                else "unsupported"
            )
            db.execute(
                "UPDATE submissions SET phase=?,updated_at=? WHERE work_id=?",
                (phase, utc_now(), work_id),
            )
            queued, queue_created = self._enqueue_ready(db, work_id) if remaining == 0 else (False, False)
        return {
            "ok": True,
            "duplicate": False,
            "failedAttachment": True,
            "unsupported": state == "unsupported",
            "quarantined": state == "quarantined",
            "errorClass": safe_reason,
            "phase": phase,
            "queued": queued,
            "queueCreated": queue_created,
        }

    def accept_download(self, *, work_id: str, attachment_id: str, token: str, source_path: Path) -> dict[str, Any]:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db, self.transaction(db):
            intent = db.execute(
                "SELECT * FROM attachment_intents WHERE id=? AND work_id=?",
                (attachment_id, work_id),
            ).fetchone()
            if not intent or not hmac.compare_digest(str(intent["token_hash"]), token_hash):
                raise BrainAuthorizationError("download-capability-invalid")
            if intent["failure_reason"] and not intent["consumed_at"]:
                raise BrainAuthorizationError("download-capability-failure-pending")
            if intent["consumed_at"]:
                linked = db.execute(
                    """SELECT a.quarantine_reason FROM submission_artifacts sa
                       JOIN artifacts a ON a.digest=sa.digest
                       WHERE sa.work_id=? AND sa.attachment_id=?""",
                    (work_id, attachment_id),
                ).fetchone()
                quarantined = bool(linked and linked["quarantine_reason"])
                queued, _ = self._enqueue_ready(db, work_id)
                current = db.execute(
                    "SELECT phase FROM submissions WHERE work_id=?", (work_id,),
                ).fetchone()
                return {
                    "ok": not quarantined, "duplicate": True,
                    "stored": bool(linked) and not quarantined,
                    "quarantined": quarantined,
                    "phase": str(current["phase"]) if current else "stored",
                    "queued": queued,
                }
            submission = db.execute(
                "SELECT phase,cancel_requested,user_cancel_requested FROM submissions WHERE work_id=?",
                (work_id,),
            ).fetchone()
            if (
                not submission
                or submission["phase"] in {"quarantined", "forgotten"}
                or submission["cancel_requested"]
                or submission["user_cancel_requested"]
            ):
                raise BrainAuthorizationError("download-work-not-active")
            claimed = db.execute(
                """UPDATE attachment_intents SET state='downloading'
                   WHERE id=? AND work_id=? AND state='receipt_pending' AND consumed_at IS NULL""",
                (attachment_id, work_id),
            ).rowcount
            if claimed != 1:
                raise BrainAuthorizationError("download-capability-in-use")
            db.execute("UPDATE submissions SET phase='downloading',updated_at=? WHERE work_id=?", (utc_now(), work_id))

        staged_path: Path | None = None
        published_destination: Path | None = None
        try:
            staged_path, info, cleanup_source = self._stage_download(source_path)
            cleanup_fingerprint = self._cleanup_fingerprint(info)
            digest = sha256_file(staged_path)
            media_class, detected_mime, warnings = detect_media_type(staged_path)
            declared_mime = clean_text(intent["declared_mime"], 120).lower()
            if declared_mime and declared_mime != "application/octet-stream" and declared_mime != detected_mime:
                warnings.append("content-signature-overrode-declared-mime")
            quarantine_reason = ""
            try:
                if media_class == "executable":
                    raise BrainSafetyError("executable-content")
                if detected_mime == "application/pdf":
                    warnings.extend(inspect_pdf(staged_path))
                    if "encrypted-document" in warnings:
                        raise BrainSafetyError("encrypted-document")
                    if "active-content-isolated" in warnings:
                        raise BrainSafetyError("active-content-isolated")
                if detected_mime.endswith("zip") or media_class in {"archive", "office-document", "spreadsheet", "presentation"}:
                    warnings.extend(inspect_zip(staged_path))
                    if "active-content-isolated" in warnings:
                        raise BrainSafetyError("active-content-isolated")
            except (BrainSafetyError, zipfile.BadZipFile) as exc:
                quarantine_reason = clean_text(str(exc), 80) if isinstance(exc, BrainSafetyError) else "malformed-archive"

            extension = {
                "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
                "image/webp": ".webp", "application/pdf": ".pdf", "audio/ogg": ".ogg",
                "audio/mpeg": ".mp3", "video/mp4": ".mp4", "application/zip": ".zip",
            }.get(detected_mime, ".bin")
            destination_root = self.quarantine if quarantine_reason else self.cas
            destination_dir = destination_root / digest[:2]
            destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(destination_dir, 0o700)
            destination = destination_dir / f"{digest}{extension}"
            if destination.exists() or destination.is_symlink():
                destination = self._verified_private_artifact(destination, digest)
                staged_path.unlink()
            else:
                os.replace(staged_path, destination)
                os.chmod(destination, 0o600)
                published_destination = destination
            staged_path = None

            with self.connect() as db, self.transaction(db):
                consumed = db.execute(
                    """UPDATE attachment_intents SET state='cleanup_pending',consumed_at=?,
                              source_cleanup_state='pending',source_cleanup_path=?,
                              source_cleanup_fingerprint=?
                       WHERE id=? AND work_id=? AND state='downloading' AND consumed_at IS NULL""",
                    (
                        utc_now(), str(cleanup_source), cleanup_fingerprint, attachment_id, work_id,
                    ),
                ).rowcount
                if consumed != 1:
                    raise BrainAuthorizationError("download-capability-consume-race-lost")
                existing = db.execute(
                    "SELECT ref_count,stored_path FROM artifacts WHERE digest=?",
                    (digest,),
                ).fetchone()
                if existing:
                    existing_path = self._verified_private_artifact(Path(existing["stored_path"]), digest)
                    linked = db.execute(
                        "INSERT OR IGNORE INTO submission_artifacts VALUES(?,?,?)",
                        (work_id, attachment_id, digest),
                    ).rowcount
                    if linked == 1:
                        db.execute("UPDATE artifacts SET ref_count=ref_count+1 WHERE digest=?", (digest,))
                    if existing_path != destination:
                        if published_destination == destination:
                            destination.unlink(missing_ok=True)
                            published_destination = None
                        destination = existing_path
                else:
                    db.execute(
                        "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)",
                        (digest, str(destination), media_class, detected_mime, info.st_size, 1, quarantine_reason, utc_now()),
                    )
                    db.execute(
                        "INSERT INTO submission_artifacts VALUES(?,?,?)",
                        (work_id, attachment_id, digest),
                    )
                db.execute(
                    "UPDATE submissions SET phase='downloading',updated_at=? WHERE work_id=?",
                    (utc_now(), work_id),
                )

            try:
                self._remove_gateway_download(
                    cleanup_source,
                    expected_fingerprint=cleanup_fingerprint,
                )
            except BrainSafetyError:
                with self.connect() as db, self.transaction(db):
                    db.execute(
                        """UPDATE attachment_intents
                              SET state='quarantined',failure_reason='corrupt',source_cleanup_state='failed'
                            WHERE id=? AND work_id=? AND source_cleanup_state='pending'""",
                        (attachment_id, work_id),
                    )
                    remaining = int(db.execute(
                        "SELECT COUNT(*) FROM attachment_intents WHERE work_id=? AND consumed_at IS NULL",
                        (work_id,),
                    ).fetchone()[0])
                    phase = "downloading" if remaining else "quarantined"
                    db.execute(
                        "UPDATE submissions SET phase=?,updated_at=? WHERE work_id=?",
                        (phase, utc_now(), work_id),
                    )
                    queued, queue_created = self._enqueue_ready(db, work_id) if remaining == 0 else (False, False)
                self._refresh_private_objective(work_id)
                return {
                    "ok": False,
                    "duplicate": bool(existing),
                    "stored": False,
                    "quarantined": True,
                    "cleanupPending": True,
                    "phase": phase,
                    "mediaClass": media_class,
                    "errorClass": "source-cleanup-failed",
                    "warningCount": len(set(warnings)),
                    "queued": queued,
                    "queueCreated": queue_created,
                }

            with self.connect() as db, self.transaction(db):
                completed = db.execute(
                    """UPDATE attachment_intents SET state=?,source_cleanup_state='cleaned',
                              source_cleanup_path='',source_cleanup_fingerprint=''
                         WHERE id=? AND work_id=? AND source_cleanup_state='pending'""",
                    (
                        "quarantined" if quarantine_reason else "stored",
                        attachment_id, work_id,
                    ),
                ).rowcount
                if completed != 1:
                    raise BrainAuthorizationError("download-cleanup-consume-race-lost")
                remaining = int(db.execute(
                    "SELECT COUNT(*) FROM attachment_intents WHERE work_id=? AND consumed_at IS NULL",
                    (work_id,),
                ).fetchone()[0])
                any_quarantine = bool(db.execute(
                    """SELECT 1 FROM submission_artifacts sa JOIN artifacts a ON a.digest=sa.digest
                       WHERE sa.work_id=? AND a.quarantine_reason!='' LIMIT 1""",
                    (work_id,),
                ).fetchone())
                phase = "downloading" if remaining else "quarantined" if any_quarantine else "stored"
                db.execute("UPDATE submissions SET phase=?,updated_at=? WHERE work_id=?", (phase, utc_now(), work_id))
                queued, queue_created = self._enqueue_ready(db, work_id) if remaining == 0 else (False, False)
            self._refresh_private_objective(work_id)
            return {
                "ok": not bool(quarantine_reason), "duplicate": bool(existing),
                "stored": not bool(quarantine_reason), "quarantined": bool(quarantine_reason),
                "phase": phase, "mediaClass": media_class, "errorClass": quarantine_reason or None,
                "warningCount": len(set(warnings)), "queued": queued,
                "queueCreated": queue_created, "cleanupComplete": True,
            }
        except Exception:
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)
            if published_destination is not None:
                with self.connect() as db:
                    linked = db.execute(
                        "SELECT 1 FROM artifacts WHERE stored_path=?",
                        (str(published_destination),),
                    ).fetchone()
                if not linked:
                    published_destination.unlink(missing_ok=True)
            self._release_download_claim(work_id, attachment_id)
            raise

    def _replace_extraction_chunks(
        self,
        db: sqlite3.Connection,
        *,
        extraction_id: str,
        work_id: str,
        text: str,
        confidence: float,
        coverage: str,
    ) -> int:
        chunk_ids = [
            str(row["id"])
            for row in db.execute(
                "SELECT id FROM source_chunks WHERE extraction_id=?", (extraction_id,),
            ).fetchall()
        ]
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            db.execute(f"DELETE FROM source_vectors WHERE chunk_id IN ({placeholders})", chunk_ids)
        db.execute("DELETE FROM source_chunk_fts WHERE work_id=? AND chunk_id IN (SELECT id FROM source_chunks WHERE extraction_id=?)", (work_id, extraction_id))
        db.execute("DELETE FROM source_chunks WHERE extraction_id=?", (extraction_id,))
        chunks = bounded_chunks(text)
        for ordinal, chunk in enumerate(chunks):
            text_hash = hashlib.sha256(chunk.encode()).hexdigest()
            chunk_id = stable_id(
                "brain-chunk", extraction_id, ordinal, text_hash, length=28,
            )
            vector, norm = local_embedding(chunk)
            provenance_ref = stable_id("source-chunk", work_id, extraction_id, ordinal, length=28)
            db.execute(
                """INSERT INTO source_chunks(
                     id,extraction_id,work_id,ordinal,text_private,text_hash,token_count,
                     confidence,coverage,provenance_ref,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    chunk_id, extraction_id, work_id, ordinal, chunk, text_hash,
                    len(semantic_tokens(chunk)), float(confidence), clean_text(coverage, 80),
                    provenance_ref, utc_now(),
                ),
            )
            db.execute(
                "INSERT INTO source_chunk_fts VALUES(?,?,?)",
                (chunk_id, work_id, chunk),
            )
            db.execute(
                "INSERT INTO source_vectors VALUES(?,?,?,?,?,?)",
                (
                    chunk_id, EMBEDDING_VERSION, EMBEDDING_DIMENSIONS,
                    json.dumps(vector, separators=(",", ":")), norm, utc_now(),
                ),
            )
        return len(chunks)

    def extract_submission(
        self,
        work_id: str,
        *,
        max_extracted_chars: int = MAX_EXTRACTED_CHARS,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        bounded_char_limit = max(1, min(int(max_extracted_chars), MAX_EXTRACTED_CHARS))
        with self.connect() as db, self.transaction(db):
            submission = db.execute("SELECT * FROM submissions WHERE work_id=?", (work_id,)).fetchone()
            if not submission:
                raise BrainIntakeError("unknown-work")
            if submission["phase"] in {"quarantined", "forgotten"}:
                return {"ok": submission["phase"] == "forgotten", "phase": submission["phase"], "extracted": 0}
            if submission["cancel_requested"] or submission["user_cancel_requested"]:
                raise BrainIntakeError("source-forget-in-progress")
            remaining = int(db.execute(
                "SELECT COUNT(*) FROM attachment_intents WHERE work_id=? AND consumed_at IS NULL",
                (work_id,),
            ).fetchone()[0])
            if remaining:
                raise BrainIntakeError("submission-downloads-incomplete")
            db.execute("UPDATE submissions SET phase='scanning',updated_at=? WHERE work_id=?", (utc_now(), work_id))
            rows = db.execute(
                """SELECT DISTINCT a.* FROM submission_artifacts sa JOIN artifacts a ON a.digest=sa.digest
                   WHERE sa.work_id=? ORDER BY a.digest""",
                (work_id,),
            ).fetchall()
        extracted_artifact_count = extracted_chars = supported_count = injection_count = 0
        chunk_count = vector_count = 0
        executed_routes: set[str] = set()
        warnings_total: list[str] = []
        objective_evidence = ""
        for artifact in rows:
            if artifact["quarantine_reason"]:
                continue
            with self.connect() as db:
                current = db.execute(
                    "SELECT phase,cancel_requested,user_cancel_requested FROM submissions WHERE work_id=?", (work_id,),
                ).fetchone()
            if (
                not current
                or current["cancel_requested"]
                or current["user_cancel_requested"]
                or current["phase"] == "forgotten"
            ):
                raise BrainIntakeError("source-forget-in-progress")
            try:
                artifact_path = self._verified_private_artifact(
                    Path(artifact["stored_path"]), artifact["digest"],
                )
            except BrainSafetyError as exc:
                with self.connect() as db:
                    current = db.execute(
                        "SELECT phase,cancel_requested,user_cancel_requested FROM submissions WHERE work_id=?", (work_id,),
                    ).fetchone()
                if (
                    not current
                    or current["cancel_requested"]
                    or current["user_cancel_requested"]
                    or current["phase"] == "forgotten"
                ):
                    raise BrainIntakeError("source-forget-in-progress") from exc
                with self.connect() as db, self.transaction(db):
                    db.execute(
                        "UPDATE artifacts SET quarantine_reason='artifact-integrity-failed' WHERE digest=?",
                        (artifact["digest"],),
                    )
                    db.execute(
                        "UPDATE submissions SET phase='quarantined',updated_at=? WHERE work_id=?",
                        (utc_now(), work_id),
                    )
                raise BrainSafetyError("artifact-integrity-failed")
            with self.connect() as db:
                existing_extraction = db.execute(
                    """SELECT * FROM extractions
                       WHERE work_id=? AND digest=? AND version=?""",
                    (work_id, artifact["digest"], EXTRACTION_VERSION),
                ).fetchone()
                existing_fts = bool(db.execute(
                    "SELECT 1 FROM source_fts WHERE work_id=? AND extraction_id=?",
                    (work_id, stable_id("extract", work_id, artifact["digest"], EXTRACTION_VERSION)),
                ).fetchone())
            if existing_extraction:
                try:
                    existing_warnings = json.loads(existing_extraction["warnings_json"])
                    if not isinstance(existing_warnings, list):
                        raise ValueError("warning-shape")
                    existing_path = str(existing_extraction["private_path"])
                    if existing_extraction["status"] == "indexed":
                        if not existing_path or not existing_fts:
                            raise BrainSafetyError("extraction-evidence-incomplete")
                        verified_extraction = self._verified_private_artifact(
                            Path(existing_path), str(existing_extraction["text_hash"]),
                        )
                        existing_text = verified_extraction.read_text(encoding="utf-8", errors="replace")
                        if not objective_evidence:
                            objective_evidence = existing_text
                        existing_chars = len(existing_text)
                        with self.connect() as chunk_db:
                            existing_chunk_count = int(chunk_db.execute(
                                "SELECT COUNT(*) FROM source_chunks WHERE extraction_id=?",
                                (existing_extraction["id"],),
                            ).fetchone()[0])
                            existing_vector_count = int(chunk_db.execute(
                                """SELECT COUNT(*) FROM source_vectors
                                   WHERE chunk_id IN (SELECT id FROM source_chunks WHERE extraction_id=?)""",
                                (existing_extraction["id"],),
                            ).fetchone()[0])
                        if not existing_chunk_count or existing_vector_count != existing_chunk_count:
                            with self.connect() as chunk_db, self.transaction(chunk_db):
                                existing_chunk_count = self._replace_extraction_chunks(
                                    chunk_db,
                                    extraction_id=str(existing_extraction["id"]),
                                    work_id=work_id,
                                    text=existing_text,
                                    confidence=float(existing_extraction["confidence"]),
                                    coverage=str(existing_extraction["coverage"]),
                                )
                            existing_vector_count = existing_chunk_count
                    else:
                        if existing_path:
                            raise BrainSafetyError("unsupported-extraction-path-invalid")
                        existing_chars = 0
                except (BrainSafetyError, OSError, ValueError, json.JSONDecodeError):
                    with self.connect() as db, self.transaction(db):
                        db.execute(
                            """DELETE FROM source_vectors WHERE chunk_id IN
                               (SELECT id FROM source_chunks WHERE extraction_id=?)""",
                            (existing_extraction["id"],),
                        )
                        db.execute(
                            """DELETE FROM source_chunk_fts WHERE chunk_id IN
                               (SELECT id FROM source_chunks WHERE extraction_id=?)""",
                            (existing_extraction["id"],),
                        )
                        db.execute(
                            "DELETE FROM source_chunks WHERE extraction_id=?",
                            (existing_extraction["id"],),
                        )
                        db.execute("DELETE FROM source_fts WHERE extraction_id=?", (existing_extraction["id"],))
                        db.execute("DELETE FROM extractions WHERE id=?", (existing_extraction["id"],))
                else:
                    extracted_artifact_count += 1
                    extracted_chars += min(
                        int(existing_chars), max(0, bounded_char_limit - extracted_chars),
                    )
                    supported_count += int(existing_extraction["status"] == "indexed")
                    injection_count += int(existing_extraction["prompt_injection"])
                    chunk_count += existing_chunk_count if existing_extraction["status"] == "indexed" else 0
                    vector_count += existing_vector_count if existing_extraction["status"] == "indexed" else 0
                    executed_routes.add(str(existing_extraction["model_route"] or "local-none"))
                    warnings_total.extend(str(value) for value in existing_warnings)
                    continue
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise BrainIntakeError("extraction-time-budget")
            try:
                remaining_seconds = (
                    max(1.0, deadline_monotonic - time.monotonic())
                    if deadline_monotonic is not None else None
                )
                result = extract_local(
                    artifact_path,
                    artifact["media_class"],
                    artifact["detected_mime"],
                    timeout_seconds=remaining_seconds,
                )
            except Exception as exc:
                with self.connect() as db:
                    current = db.execute(
                        "SELECT phase,cancel_requested,user_cancel_requested FROM submissions WHERE work_id=?", (work_id,),
                    ).fetchone()
                if (
                    not current
                    or current["cancel_requested"]
                    or current["user_cancel_requested"]
                    or current["phase"] == "forgotten"
                ):
                    raise BrainIntakeError("source-forget-in-progress") from exc
                raise
            remaining_chars = max(0, bounded_char_limit - extracted_chars)
            result_text = str(result["text"])
            if result_text and not objective_evidence:
                objective_evidence = result_text
            if len(result_text) > remaining_chars:
                result_text = result_text[:remaining_chars]
                result["warnings"] = sorted(set(result["warnings"]) | {"submission-extraction-truncated"})
                result["text"] = result_text
                result["supported"] = bool(result_text)
            extraction_id = stable_id("extract", work_id, artifact["digest"], EXTRACTION_VERSION)
            private_path = self.extracted / f"{extraction_id}.txt"
            pending_path: Path | None = None
            if result["text"]:
                pending_path = self.staging / f".{extraction_id}.{uuid.uuid4().hex}.pending"
                atomic_write(pending_path, str(result["text"]).encode("utf-8"), 0o600)
            text_hash = hashlib.sha256(str(result["text"]).encode()).hexdigest()
            try:
                with self.connect() as db, self.transaction(db):
                    current = db.execute(
                        "SELECT phase,cancel_requested,user_cancel_requested FROM submissions WHERE work_id=?", (work_id,),
                    ).fetchone()
                    if (
                        not current
                        or current["cancel_requested"]
                        or current["user_cancel_requested"]
                        or current["phase"] == "forgotten"
                    ):
                        raise BrainIntakeError("source-forget-in-progress")
                    if pending_path is not None:
                        os.replace(pending_path, private_path)
                        os.chmod(private_path, 0o600)
                        pending_path = None
                    db.execute(
                        """INSERT INTO extractions(
                             id,work_id,digest,version,method,status,private_path,text_hash,
                             confidence,coverage,warnings_json,prompt_injection,created_at,
                             model_route,tool_version
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET
                             method=excluded.method,status=excluded.status,
                             private_path=excluded.private_path,text_hash=excluded.text_hash,
                             confidence=excluded.confidence,coverage=excluded.coverage,
                             warnings_json=excluded.warnings_json,
                             prompt_injection=excluded.prompt_injection,
                             created_at=excluded.created_at,model_route=excluded.model_route,
                             tool_version=excluded.tool_version""",
                        (
                            extraction_id, work_id, artifact["digest"], EXTRACTION_VERSION,
                            result["method"], "indexed" if result["supported"] else "unsupported",
                            str(private_path) if result["text"] else "", text_hash,
                            result["confidence"], result["coverage"], json.dumps(result["warnings"]),
                            int(result["promptInjection"]), utc_now(),
                            clean_text(result.get("modelRoute"), 80) or "local-none",
                            clean_text(result.get("toolVersion"), 120) or "n/a",
                        ),
                    )
                    db.execute("DELETE FROM source_fts WHERE extraction_id=?", (extraction_id,))
                    if result["text"]:
                        db.execute(
                            "INSERT INTO source_fts VALUES(?,?,?)",
                            (extraction_id, work_id, result["text"]),
                        )
                        created_chunks = self._replace_extraction_chunks(
                            db,
                            extraction_id=extraction_id,
                            work_id=work_id,
                            text=str(result["text"]),
                            confidence=float(result["confidence"]),
                            coverage=str(result["coverage"]),
                        )
                    else:
                        created_chunks = self._replace_extraction_chunks(
                            db,
                            extraction_id=extraction_id,
                            work_id=work_id,
                            text="",
                            confidence=float(result["confidence"]),
                            coverage=str(result["coverage"]),
                        )
            finally:
                if pending_path is not None:
                    pending_path.unlink(missing_ok=True)
            extracted_artifact_count += 1
            extracted_chars += len(str(result["text"]))
            supported_count += int(result["supported"])
            injection_count += int(result["promptInjection"])
            chunk_count += created_chunks
            vector_count += created_chunks
            executed_routes.add(str(result.get("modelRoute") or "local-none"))
            warnings_total.extend(result["warnings"])
        phase = "indexed" if supported_count else "unsupported"
        with self.connect() as db, self.transaction(db):
            current = db.execute(
                "SELECT phase,cancel_requested,user_cancel_requested FROM submissions WHERE work_id=?", (work_id,),
            ).fetchone()
            if (
                not current
                or current["cancel_requested"]
                or current["user_cancel_requested"]
                or current["phase"] == "forgotten"
            ):
                raise BrainIntakeError("source-forget-in-progress")
            db.execute("UPDATE submissions SET phase=?,updated_at=? WHERE work_id=?", (phase, utc_now(), work_id))
        self._refresh_private_objective(work_id, extracted_text=objective_evidence)
        return {
            "ok": True, "phase": phase,
            "extracted": extracted_artifact_count,
            "extractedChars": extracted_chars,
            "supported": supported_count, "promptInjectionSignals": injection_count,
            "warningCount": len(set(warnings_total)),
            "chunkCount": chunk_count,
            "vectorCount": vector_count,
            "routes": sorted(executed_routes),
        }

    def _candidate_relationship(
        self,
        *,
        candidate_type: str,
        subject: str,
        predicate: str,
        value: str,
        deadline_monotonic: float | None = None,
    ) -> tuple[str, str, float]:
        enforce_deadline(deadline_monotonic)
        normalized_type = "fact" if candidate_type == "date" else candidate_type
        with self.connect() as db:
            local_rows = [
                {
                    "id": str(row["id"]),
                    "type": str(row["candidate_type"]),
                    "subject": str(row["subject"]),
                    "predicate": str(row["predicate"]),
                    "value": str(row["value_private"]),
                }
                for row in db.execute(
                    """SELECT id,candidate_type,subject,predicate,value_private FROM candidates
                       WHERE status NOT IN ('forgotten','rejected')""",
                ).fetchall()
            ]
        import memory_registry
        registry_db = memory_registry.connect()
        try:
            registry_rows = [
                {
                    "id": str(row["id"]),
                    "type": str(row["memory_type"]),
                    "subject": str(row["subject"]),
                    "predicate": str(row["predicate"]),
                    "value": str(row["object_text"]),
                }
                for row in registry_db.execute(
                    """SELECT id,memory_type,subject,predicate,object_text FROM memory_records
                       WHERE status='active'
                       UNION ALL
                       SELECT id,memory_type,subject,predicate,object_text FROM memory_candidates
                       WHERE status IN ('candidate','active','disputed')""",
                ).fetchall()
            ]
        finally:
            registry_db.close()
        wanted_key = set(semantic_tokens(f"{subject} {predicate}"))
        wanted_value = set(semantic_tokens(value))
        wanted_key_vector, _ = local_embedding(f"{subject} {predicate}")
        wanted_value_vector, _ = local_embedding(value)
        best_duplicate: tuple[str, float] | None = None
        best_conflict: tuple[str, float] | None = None
        for row in [*local_rows, *registry_rows]:
            enforce_deadline(deadline_monotonic)
            if str(row["type"]) != normalized_type:
                continue
            row_key = set(semantic_tokens(f"{row['subject']} {row['predicate']}"))
            row_value = set(semantic_tokens(row["value"]))
            if not wanted_key or not row_key:
                continue
            key_jaccard = len(wanted_key & row_key) / len(wanted_key | row_key)
            key_score = max(
                key_jaccard,
                cosine_similarity(wanted_key_vector, local_embedding(f"{row['subject']} {row['predicate']}")[0]),
            )
            if key_score < SEMANTIC_KEY_THRESHOLD:
                continue
            value_jaccard = (
                len(wanted_value & row_value) / len(wanted_value | row_value)
                if wanted_value and row_value else 0.0
            )
            value_score = max(
                value_jaccard,
                cosine_similarity(wanted_value_vector, local_embedding(row["value"])[0]),
            )
            combined = (key_score + value_score) / 2.0
            if value_score >= SEMANTIC_DUPLICATE_THRESHOLD:
                if best_duplicate is None or combined > best_duplicate[1]:
                    best_duplicate = (str(row["id"]), combined)
            elif key_jaccard >= 0.9 and value_jaccard <= 0.35:
                if best_conflict is None or key_score > best_conflict[1]:
                    best_conflict = (str(row["id"]), key_score)
        if best_duplicate:
            return best_duplicate[0], "", round(best_duplicate[1], 6)
        if best_conflict:
            return "", best_conflict[0], round(best_conflict[1], 6)
        return "", "", 0.0

    def propose_candidate(
        self,
        *,
        work_id: str,
        candidate_type: str,
        subject: str,
        predicate: str,
        value: str,
        privacy: str,
        confidence: float,
        manual_reason: str = "",
        duplicate_of: str = "",
        conflicts_with: str = "",
        semantic_score: float = 0.0,
    ) -> dict[str, Any]:
        registry_args: dict[str, Any] | None = None
        injection = 0
        normalized_type = clean_text(candidate_type, 40).strip().lower()
        if normalized_type == "date":
            normalized_type = "fact"
        normalized_manual_reason = clean_text(manual_reason, 80).strip().lower()
        try:
            normalized_confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError) as exc:
            raise BrainIntakeError("candidate-confidence-invalid") from exc
        with self.connect() as db, self.transaction(db):
            submission = db.execute("SELECT * FROM submissions WHERE work_id=?", (work_id,)).fetchone()
            if (
                not submission
                or submission["phase"] in {"forgotten", "quarantined"}
                or submission["cancel_requested"]
                or submission["user_cancel_requested"]
            ):
                raise BrainIntakeError("source-not-candidate-eligible")
            requested_privacy = privacy if privacy in PRIVACY_CLASSES else "private"
            source_privacy = submission["privacy_class"] if submission["privacy_class"] in PRIVACY_CLASSES else "private"
            privacy_rank = {"private": 0, "internal": 1, "dashboard-safe": 2}
            effective_privacy = min(
                (requested_privacy, source_privacy),
                key=lambda value: privacy_rank[value],
            )
            if submission["reference_only"]:
                eligibility = "reference-only"
                status = "blocked"
            elif duplicate_of:
                eligibility = "semantic-duplicate"
                status = "blocked"
            else:
                extraction = db.execute(
                    """SELECT COUNT(*) AS total,
                              SUM(status='indexed') AS indexed,
                              SUM(prompt_injection=1) AS injection
                       FROM extractions WHERE work_id=?""",
                    (work_id,),
                ).fetchone()
                indexed = int(extraction["indexed"] or 0)
                injection = int(extraction["injection"] or 0)
                if int(extraction["total"] or 0) == 0:
                    raise BrainIntakeError("source-extraction-required")
                if conflicts_with:
                    normalized_manual_reason = "conflict-requires-review"
                auto = (
                    normalized_type in AUTO_ELIGIBLE_TYPES
                    and effective_privacy in {"internal", "dashboard-safe"}
                    and normalized_confidence >= 0.95 and indexed > 0 and injection == 0
                    and not normalized_manual_reason
                )
                eligibility = (
                    "verified-low-risk"
                    if auto else normalized_manual_reason or "manual-review-required"
                )
                status = "eligible" if auto else "pending"
            candidate_id = stable_id(
                "brain-candidate", work_id, normalized_type, subject, predicate, value, length=28,
            )
            inserted = db.execute(
                """INSERT OR IGNORE INTO candidates(
                     id,work_id,candidate_type,subject,predicate,value_private,privacy_class,
                     confidence,provenance_ref,status,eligibility_reason,registry_candidate_id,
                     registry_memory_id,created_at,duplicate_of,conflicts_with,semantic_score
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    candidate_id, work_id, normalized_type, clean_text(subject, 240),
                    clean_text(predicate, 160), clean_text(value, 4000),
                    effective_privacy, normalized_confidence,
                    stable_id("source-evidence", work_id, length=28), status, eligibility, "", "", utc_now(),
                    clean_text(duplicate_of, 120), clean_text(conflicts_with, 120),
                    max(0.0, min(1.0, float(semantic_score or 0.0))),
                ),
            ).rowcount == 1
            db.execute("UPDATE submissions SET phase='candidate_pending',updated_at=? WHERE work_id=?", (utc_now(), work_id))
            if status != "blocked":
                registry_type = (
                    normalized_type
                    if normalized_type in AUTO_ELIGIBLE_TYPES | {"decision", "preference", "procedure", "episode"}
                    else "procedure"
                )
                registry_args = {
                    "agent": "josh2", "type": registry_type,
                    "subject": clean_text(subject, 240), "predicate": clean_text(predicate, 160),
                    "value": clean_text(value, 4000), "owner": "josh2", "visibility": "ecosystem",
                    "privacy": effective_privacy,
                    "source": f"brain-source:{work_id}", "source_ref": f"brain-source:{work_id}",
                    "source_kind": "brain-source", "evidence": stable_id("source-evidence", work_id, length=28),
                    "confidence": normalized_confidence,
                    "extraction_version": EXTRACTION_VERSION,
                    "governance_eligible": eligibility == "verified-low-risk",
                    "injection_status": "flagged" if injection else "clear",
                    "source_state": "active", "metadata": {"referenceOnly": False},
                }
        registry_candidate_id = ""
        if registry_args:
            import argparse as _argparse
            import memory_registry
            registry_db = memory_registry.connect()
            try:
                registry_candidate_id = str(memory_registry.propose(registry_db, _argparse.Namespace(**registry_args)).get("id") or "")
            finally:
                registry_db.close()
            with self.connect() as db, self.transaction(db):
                db.execute("UPDATE candidates SET registry_candidate_id=? WHERE id=?", (registry_candidate_id, candidate_id))
        with self.connect() as db:
            persisted = db.execute(
                "SELECT status,eligibility_reason,registry_candidate_id FROM candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
        return {
            "ok": True, "candidateId": candidate_id, "status": str(persisted["status"]),
            "eligibility": str(persisted["eligibility_reason"]),
            "registryProposed": bool(persisted["registry_candidate_id"]),
            "duplicate": not inserted,
        }

    def synthesize_candidates(
        self,
        work_id: str,
        *,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        """Create deterministic candidates only from explicit source statements.

        Extracted media is untrusted data, so this parser deliberately does not
        infer facts from prose.  It accepts bounded ``Type: subject | predicate |
        value`` rows, preserves provenance, and routes risky rows to review.
        """
        enforce_deadline(deadline_monotonic)
        with self.connect() as db:
            submission = db.execute(
                """SELECT phase,privacy_class,reference_only,cancel_requested,user_cancel_requested
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
            extractions = db.execute(
                """SELECT private_path,text_hash,prompt_injection FROM extractions
                     WHERE work_id=? AND status='indexed' ORDER BY id""",
                (work_id,),
            ).fetchall()
        if not submission or submission["phase"] in {"forgotten", "quarantined"}:
            raise BrainIntakeError("source-not-candidate-eligible")
        if submission["cancel_requested"] or submission["user_cancel_requested"]:
            raise BrainIntakeError("source-forget-in-progress")
        parsed: list[tuple[str, str, str, str, str]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for extraction in extractions:
            enforce_deadline(deadline_monotonic)
            private_path = str(extraction["private_path"] or "")
            if not private_path:
                continue
            verified = self._verified_private_artifact(Path(private_path), str(extraction["text_hash"]))
            text = verified.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                enforce_deadline(deadline_monotonic)
                match = CANDIDATE_LINE_RE.fullmatch(line)
                if not match:
                    continue
                candidate_type = match.group(1).lower()
                if candidate_type == "date":
                    candidate_type = "fact"
                subject = clean_text(match.group(2), 240)
                predicate = clean_text(match.group(3), 160)
                value = clean_text(match.group(4), 4000)
                key = (candidate_type, subject, predicate, value)
                if not all((subject, predicate, value)) or key in seen:
                    continue
                seen.add(key)
                combined = " ".join(key)
                if SENSITIVE_CANDIDATE_RE.search(combined):
                    manual_reason = "sensitive-fact-requires-review"
                elif UNCERTAIN_CANDIDATE_RE.search(combined):
                    manual_reason = "uncertain-inference-requires-review"
                elif candidate_type in MANUAL_REVIEW_TYPES:
                    manual_reason = f"{candidate_type}-requires-review"
                elif bool(extraction["prompt_injection"]):
                    manual_reason = "prompt-injection-requires-review"
                else:
                    manual_reason = ""
                parsed.append((candidate_type, subject, predicate, value, manual_reason))
                if len(parsed) >= MAX_SYNTHESIZED_CANDIDATES:
                    break
            if len(parsed) >= MAX_SYNTHESIZED_CANDIDATES:
                break
        created = duplicates = conflicts = 0
        for candidate_type, subject, predicate, value, manual_reason in parsed:
            enforce_deadline(deadline_monotonic)
            with self.connect() as db:
                before = int(db.execute(
                    "SELECT COUNT(*) FROM candidates WHERE work_id=?", (work_id,),
                ).fetchone()[0])
            duplicate_of, conflicts_with, semantic_score = self._candidate_relationship(
                candidate_type=candidate_type,
                subject=subject,
                predicate=predicate,
                value=value,
                deadline_monotonic=deadline_monotonic,
            )
            if conflicts_with:
                manual_reason = "conflict-requires-review"
            result = self.propose_candidate(
                work_id=work_id,
                candidate_type=candidate_type,
                subject=subject,
                predicate=predicate,
                value=value,
                privacy=str(submission["privacy_class"]),
                confidence=0.99 if not manual_reason else 0.85,
                manual_reason=manual_reason,
                duplicate_of=duplicate_of,
                conflicts_with=conflicts_with,
                semantic_score=semantic_score,
            )
            enforce_deadline(deadline_monotonic)
            with self.connect() as db:
                after = int(db.execute(
                    "SELECT COUNT(*) FROM candidates WHERE work_id=?", (work_id,),
                ).fetchone()[0])
            created += int(after > before)
            duplicates += int(result["eligibility"] == "semantic-duplicate")
            conflicts += int(result["eligibility"] == "conflict-requires-review")
        with self.connect() as db:
            rows = db.execute(
                "SELECT status FROM candidates WHERE work_id=?", (work_id,),
            ).fetchall()
        return {
            "ok": True,
            "parsed": len(parsed),
            "created": created,
            "candidateCount": len(rows),
            "eligible": sum(row["status"] == "eligible" for row in rows),
            "pending": sum(row["status"] == "pending" for row in rows),
            "blocked": sum(row["status"] == "blocked" for row in rows),
            "semanticDuplicates": duplicates,
            "conflicts": conflicts,
        }

    def review_candidates(
        self,
        work_id: str,
        *,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        """Apply canonical safe review and reconcile only this source locally."""
        enforce_deadline(deadline_monotonic)
        with self.connect() as db:
            submission = db.execute(
                """SELECT phase,reference_only,cancel_requested,user_cancel_requested
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
            local_rows = db.execute(
                "SELECT id,registry_candidate_id,status FROM candidates WHERE work_id=?",
                (work_id,),
            ).fetchall()
        if not submission or submission["phase"] == "forgotten":
            raise BrainIntakeError("source-not-candidate-eligible")
        if submission["cancel_requested"] or submission["user_cancel_requested"]:
            raise BrainIntakeError("source-forget-in-progress")
        registry_ids = [
            str(row["registry_candidate_id"])
            for row in local_rows if row["registry_candidate_id"]
        ]
        if not registry_ids or submission["reference_only"]:
            return {
                "ok": True,
                "candidateCount": len(local_rows),
                "promoted": 0,
                "pending": sum(row["status"] in {"pending", "eligible"} for row in local_rows),
                "conflicts": 0,
            }
        import memory_registry
        registry_db = memory_registry.connect()
        try:
            enforce_deadline(deadline_monotonic)
            memory_registry.review(registry_db, apply_safe=True)
            enforce_deadline(deadline_monotonic)
            placeholders = ",".join("?" for _ in registry_ids)
            reviewed = registry_db.execute(
                f"""SELECT c.id,c.status,r.id AS memory_id
                       FROM memory_candidates c
                       LEFT JOIN memory_records r ON r.content_hash=c.content_hash AND r.status='active'
                      WHERE c.id IN ({placeholders})""",
                registry_ids,
            ).fetchall()
        finally:
            registry_db.close()
        reviewed_by_id = {str(row["id"]): row for row in reviewed}
        promoted = conflicts = 0
        with self.connect() as db, self.transaction(db):
            current = db.execute(
                """SELECT phase,reference_only,cancel_requested,user_cancel_requested
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
            if (
                not current
                or current["phase"] == "forgotten"
                or current["cancel_requested"]
                or current["user_cancel_requested"]
                or current["reference_only"]
            ):
                raise BrainIntakeError("source-forget-in-progress")
            for local in local_rows:
                enforce_deadline(deadline_monotonic)
                registry_id = str(local["registry_candidate_id"] or "")
                reviewed_row = reviewed_by_id.get(registry_id)
                if not reviewed_row:
                    continue
                registry_status = str(reviewed_row["status"])
                if registry_status == "active" and reviewed_row["memory_id"]:
                    db.execute(
                        """UPDATE candidates SET status='active',eligibility_reason='auto-promoted',
                                  registry_memory_id=? WHERE id=?""",
                        (str(reviewed_row["memory_id"]), local["id"]),
                    )
                    promoted += 1
                elif registry_status == "disputed":
                    db.execute(
                        """UPDATE candidates SET status='pending',
                                  eligibility_reason='conflict-requires-review' WHERE id=?""",
                        (local["id"],),
                    )
                    conflicts += 1
        with self.connect() as db:
            rows = db.execute(
                "SELECT status FROM candidates WHERE work_id=?", (work_id,),
            ).fetchall()
        return {
            "ok": True,
            "candidateCount": len(rows),
            "promoted": promoted,
            "pending": sum(row["status"] in {"pending", "eligible"} for row in rows),
            "conflicts": conflicts,
        }

    def mark_reference_only(self, work_id: str, *, authorized_user: str) -> dict[str, Any]:
        self._authorize_actor(authorized_user, work_id)
        with self.connect() as db:
            submission = db.execute(
                """SELECT phase,cancel_requested,user_cancel_requested
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
        if not submission or submission["phase"] == "forgotten":
            raise BrainIntakeError("unknown-or-forgotten-work")
        if submission["cancel_requested"] or submission["user_cancel_requested"]:
            raise BrainIntakeError("source-forget-in-progress")
        import argparse as _argparse
        import memory_registry
        registry_db = memory_registry.connect()
        try:
            registry_result = memory_registry.forget_source(
                registry_db,
                _argparse.Namespace(source=f"brain-source:{work_id}", actor="josh2", confirm=True),
            )
        finally:
            registry_db.close()
        with self.connect() as db, self.transaction(db):
            changed = db.execute(
                """UPDATE submissions SET reference_only=1,updated_at=?
                     WHERE work_id=? AND phase!='forgotten'
                       AND cancel_requested=0 AND user_cancel_requested=0""",
                (utc_now(), work_id),
            ).rowcount
            db.execute(
                "UPDATE candidates SET status='blocked',eligibility_reason='reference-only' WHERE work_id=? AND status IN ('eligible','pending')",
                (work_id,),
            )
        if not changed:
            raise BrainIntakeError("unknown-or-forgotten-work")
        return {
            "ok": True, "referenceOnly": True, "promotionBlocked": True,
            "memoryCandidateTombstones": int(registry_result.get("candidateCount") or 0),
            "memoryRecordTombstones": int(registry_result.get("recordCount") or 0),
        }

    def cancel_submission(self, work_id: str, *, authorized_user: str) -> dict[str, Any]:
        """Stop Brain processing while retaining its private source evidence."""
        self._authorize_actor(authorized_user, work_id)
        with self.connect() as db, self.transaction(db):
            submission = db.execute(
                """SELECT phase,cancel_requested,user_cancel_requested
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
            if not submission or submission["phase"] == "forgotten":
                raise BrainIntakeError("unknown-or-forgotten-work")
            if submission["cancel_requested"]:
                raise BrainIntakeError("source-forget-in-progress")
            terminal = db.execute(
                "SELECT outcome FROM intake_results WHERE work_id=?", (work_id,),
            ).fetchone()
            if terminal:
                return {
                    "ok": True,
                    "cancelled": False,
                    "duplicate": True,
                    "tooLate": True,
                    "terminalStatus": str(terminal["outcome"]),
                    "sourceRetained": submission["phase"] != "forgotten",
                    "revokedPending": 0,
                }
            duplicate = bool(submission["user_cancel_requested"])
            db.execute(
                "UPDATE submissions SET user_cancel_requested=1,updated_at=? WHERE work_id=?",
                (utc_now(), work_id),
            )
            for pending_intent in db.execute(
                """SELECT id FROM attachment_intents
                     WHERE work_id=? AND consumed_at IS NULL""",
                (work_id,),
            ).fetchall():
                revoked_hash = hashlib.sha256(
                    f"cancelled:{work_id}:{pending_intent['id']}:{secrets.token_hex(16)}".encode()
                ).hexdigest()
                db.execute(
                    """UPDATE attachment_intents SET state='cancelled',consumed_at=?,token_hash=?
                         WHERE id=? AND work_id=? AND consumed_at IS NULL""",
                    (utc_now(), revoked_hash, pending_intent["id"], work_id),
                )
            governed_count = int(db.execute(
                """SELECT COUNT(*) FROM candidates WHERE work_id=?
                     AND (
                       status IN ('pending','eligible','active')
                       OR registry_candidate_id!=''
                       OR registry_memory_id!=''
                     )""",
                (work_id,),
            ).fetchone()[0])
            revoked_pending = db.execute(
                """UPDATE candidates SET status='cancelled',eligibility_reason='cancelled-by-owner'
                     WHERE work_id=? AND status IN ('pending','eligible','active')""",
                (work_id,),
            ).rowcount
            db.execute(
                """UPDATE actions SET consumed_at=COALESCE(consumed_at,?)
                     WHERE work_id=? AND consumed_at IS NULL""",
                (utc_now(), work_id),
            )
            queued, queue_created = self._enqueue_ready(db, work_id, force_cancel=True)
        registry_candidates = registry_records = 0
        if governed_count:
            import argparse as _argparse
            import memory_registry
            registry_db = memory_registry.connect()
            try:
                registry_result = memory_registry.forget_source(
                    registry_db,
                    _argparse.Namespace(
                        source=f"brain-source:{work_id}", actor="josh2", confirm=True,
                    ),
                )
            finally:
                registry_db.close()
            registry_candidates = int(registry_result.get("candidateCount") or 0)
            registry_records = int(registry_result.get("recordCount") or 0)
        return {
            "ok": True,
            "cancelled": True,
            "duplicate": duplicate,
            "tooLate": False,
            "terminalStatus": "pending",
            "sourceRetained": True,
            "revokedPending": int(revoked_pending),
            "memoryCandidateTombstones": registry_candidates,
            "memoryRecordTombstones": registry_records,
            "queued": queued,
            "queueCreated": queue_created,
        }

    def privacy_change_preview(
        self,
        work_id: str,
        *,
        authorized_user: str,
        privacy: str,
    ) -> dict[str, Any]:
        normalized_user = self._authorize_actor(authorized_user, work_id)
        target = clean_text(privacy, 40)
        if target not in PRIVACY_CLASSES:
            raise BrainIntakeError("privacy-class-invalid")
        rank = {"private": 0, "internal": 1, "dashboard-safe": 2}
        with self.connect() as db, self.transaction(db):
            submission = db.execute(
                """SELECT phase,privacy_class,cancel_requested,user_cancel_requested
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
            if (
                not submission
                or submission["phase"] == "forgotten"
                or submission["cancel_requested"]
                or submission["user_cancel_requested"]
            ):
                raise BrainIntakeError("source-privacy-change-ineligible")
            current = str(submission["privacy_class"])
            if rank[target] <= rank[current]:
                return {
                    "ok": True,
                    "currentPrivacy": current,
                    "targetPrivacy": target,
                    "confirmationRequired": False,
                }
            token = secrets.token_urlsafe(28)
            expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=FORGET_TTL_SECONDS)
            db.execute(
                """INSERT INTO actions(
                     token_hash,work_id,authorized_user,action,impact_json,expires_at,consumed_at,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    hashlib.sha256(token.encode()).hexdigest(), work_id, normalized_user,
                    "privacy-change-confirm",
                    json.dumps({"from": current, "to": target}, sort_keys=True),
                    expires.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    None, utc_now(),
                ),
            )
        return {
            "ok": True,
            "currentPrivacy": current,
            "targetPrivacy": target,
            "confirmationRequired": True,
            "confirmationToken": token,
        }

    def change_privacy(
        self,
        work_id: str,
        *,
        authorized_user: str,
        privacy: str,
        confirmation_token: str = "",
    ) -> dict[str, Any]:
        normalized_user = self._authorize_actor(authorized_user, work_id)
        target = clean_text(privacy, 40)
        if target not in PRIVACY_CLASSES:
            raise BrainIntakeError("privacy-class-invalid")
        rank = {"private": 0, "internal": 1, "dashboard-safe": 2}
        with self.connect() as db:
            submission = db.execute(
                """SELECT phase,privacy_class,cancel_requested,user_cancel_requested
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
            governed_count = int(db.execute(
                """SELECT COUNT(*) FROM candidates WHERE work_id=?
                     AND status IN ('pending','eligible','active')""",
                (work_id,),
            ).fetchone()[0])
        if (
            not submission
            or submission["phase"] == "forgotten"
            or submission["cancel_requested"]
            or submission["user_cancel_requested"]
        ):
            raise BrainIntakeError("source-privacy-change-ineligible")
        current = str(submission["privacy_class"])
        if current == target:
            return {
                "ok": True,
                "duplicate": True,
                "privacy": target,
                "broadened": False,
                "revokedPending": 0,
                "governedItemsUnchanged": governed_count,
            }
        broadening = rank[target] > rank[current]
        token_hash = hashlib.sha256(confirmation_token.encode()).hexdigest()
        if broadening:
            with self.connect() as db, self.transaction(db):
                action = db.execute(
                    "SELECT * FROM actions WHERE token_hash=?", (token_hash,),
                ).fetchone()
                if (
                    not action
                    or action["work_id"] != work_id
                    or action["action"] != "privacy-change-confirm"
                    or action["consumed_at"]
                    or parse_utc(str(action["expires_at"])) < dt.datetime.now(dt.timezone.utc)
                    or not hmac.compare_digest(str(action["authorized_user"]), normalized_user)
                ):
                    raise BrainAuthorizationError("privacy-change-confirmation-invalid")
                try:
                    impact = json.loads(str(action["impact_json"]))
                except json.JSONDecodeError as exc:
                    raise BrainAuthorizationError("privacy-change-confirmation-invalid") from exc
                if impact != {"from": current, "to": target}:
                    raise BrainAuthorizationError("privacy-change-confirmation-invalid")
                fresh = db.execute(
                    """SELECT phase,privacy_class,cancel_requested,user_cancel_requested
                         FROM submissions WHERE work_id=?""",
                    (work_id,),
                ).fetchone()
                if (
                    not fresh
                    or fresh["phase"] == "forgotten"
                    or fresh["cancel_requested"]
                    or fresh["user_cancel_requested"]
                    or str(fresh["privacy_class"]) != current
                ):
                    raise BrainIntakeError("source-privacy-change-ineligible")
                consumed = db.execute(
                    "UPDATE actions SET consumed_at=? WHERE token_hash=? AND consumed_at IS NULL",
                    (utc_now(), token_hash),
                ).rowcount
                if consumed != 1:
                    raise BrainAuthorizationError("privacy-change-confirmation-invalid")
                db.execute(
                    "UPDATE submissions SET privacy_class=?,updated_at=? WHERE work_id=?",
                    (target, utc_now(), work_id),
                )
            return {
                "ok": True,
                "duplicate": False,
                "privacy": target,
                "broadened": True,
                "revokedPending": 0,
                "governedItemsUnchanged": governed_count,
            }

        registry_candidates = registry_records = 0
        if governed_count:
            import argparse as _argparse
            import memory_registry
            registry_db = memory_registry.connect()
            try:
                registry_result = memory_registry.forget_source(
                    registry_db,
                    _argparse.Namespace(
                        source=f"brain-source:{work_id}", actor="josh2", confirm=True,
                    ),
                )
            finally:
                registry_db.close()
            registry_candidates = int(registry_result.get("candidateCount") or 0)
            registry_records = int(registry_result.get("recordCount") or 0)
        with self.connect() as db, self.transaction(db):
            fresh = db.execute(
                """SELECT phase,privacy_class,cancel_requested,user_cancel_requested
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
            if (
                not fresh
                or fresh["phase"] == "forgotten"
                or fresh["cancel_requested"]
                or fresh["user_cancel_requested"]
                or str(fresh["privacy_class"]) != current
            ):
                raise BrainIntakeError("source-privacy-change-ineligible")
            revoked = db.execute(
                """UPDATE candidates SET privacy_class=?,status='blocked',
                          eligibility_reason='privacy-lowered'
                     WHERE work_id=? AND status IN ('pending','eligible','active')""",
                (target, work_id),
            ).rowcount
            db.execute(
                "UPDATE submissions SET privacy_class=?,updated_at=? WHERE work_id=?",
                (target, utc_now(), work_id),
            )
        return {
            "ok": True,
            "duplicate": False,
            "privacy": target,
            "broadened": False,
            "revokedPending": int(revoked),
            "governedItemsUnchanged": 0,
            "memoryCandidateTombstones": registry_candidates,
            "memoryRecordTombstones": registry_records,
        }

    def correct(
        self,
        work_id: str,
        *,
        subject: str,
        predicate: str,
        value: str,
        authorized_user: str,
        privacy: str = "private",
    ) -> dict[str, Any]:
        self._authorize_actor(authorized_user, work_id)
        result = self.propose_candidate(
            work_id=work_id, candidate_type="fact", subject=subject,
            predicate=predicate, value=value, privacy=privacy, confidence=0.99,
        )
        if result["status"] != "blocked":
            with self.connect() as db, self.transaction(db):
                db.execute(
                    "UPDATE candidates SET status='pending',eligibility_reason='correction-requires-review' WHERE id=?",
                    (result["candidateId"],),
                )
            result.update({"status": "pending", "eligibility": "correction-requires-review"})
        return result

    def approve_candidate(
        self,
        work_id: str,
        *,
        candidate_id: str,
        authorized_user: str,
    ) -> dict[str, Any]:
        self._authorize_actor(authorized_user, work_id)
        with self.connect() as db:
            candidate = db.execute(
                "SELECT * FROM candidates WHERE id=? AND work_id=?",
                (candidate_id, work_id),
            ).fetchone()
        if not candidate or candidate["status"] != "eligible" or not candidate["registry_candidate_id"]:
            raise BrainAuthorizationError("candidate-not-eligible-for-approval")
        import argparse as _argparse
        import memory_registry
        registry_db = memory_registry.connect()
        try:
            try:
                approved = memory_registry.approve_candidate(
                    registry_db,
                    _argparse.Namespace(
                        id=str(candidate["registry_candidate_id"]), reviewer="josh2", supersedes="",
                    ),
                )
            except SystemExit as exc:
                raise BrainIntakeError("candidate-approval-rejected") from exc
        finally:
            registry_db.close()
        with self.connect() as db, self.transaction(db):
            current = db.execute(
                """SELECT phase,cancel_requested,user_cancel_requested,reference_only
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
            if (
                not current
                or current["phase"] == "forgotten"
                or current["cancel_requested"]
                or current["user_cancel_requested"]
                or current["reference_only"]
            ):
                raise BrainIntakeError("source-not-candidate-eligible")
            changed = db.execute(
                """UPDATE candidates SET status='active',eligibility_reason='approved',
                          registry_memory_id=? WHERE id=? AND work_id=? AND status='eligible'""",
                (str(approved["recordId"]), candidate_id, work_id),
            ).rowcount
        if changed != 1:
            raise BrainIntakeError("candidate-approval-race-lost")
        return {"ok": True, "status": "active", "approved": 1}

    def reject_candidate(
        self,
        work_id: str,
        *,
        candidate_id: str,
        authorized_user: str,
        reason: str = "incorrect",
    ) -> dict[str, Any]:
        self._authorize_actor(authorized_user, work_id)
        safe_reason = reason if reason in {"incorrect", "unsupported", "outdated"} else "incorrect"
        with self.connect() as db:
            candidate = db.execute(
                "SELECT * FROM candidates WHERE id=? AND work_id=?",
                (candidate_id, work_id),
            ).fetchone()
        if (
            not candidate
            or candidate["status"] not in {"pending", "eligible"}
            or not candidate["registry_candidate_id"]
        ):
            raise BrainAuthorizationError("candidate-not-pending-review")
        import argparse as _argparse
        import memory_registry
        registry_db = memory_registry.connect()
        try:
            try:
                memory_registry.reject_candidate(
                    registry_db,
                    _argparse.Namespace(
                        id=str(candidate["registry_candidate_id"]),
                        reviewer="josh2",
                        reason=safe_reason,
                    ),
                )
            except SystemExit as exc:
                raise BrainIntakeError("candidate-rejection-failed") from exc
        finally:
            registry_db.close()
        with self.connect() as db, self.transaction(db):
            changed = db.execute(
                """UPDATE candidates SET status='rejected',eligibility_reason='rejected'
                     WHERE id=? AND work_id=? AND status IN ('pending','eligible')""",
                (candidate_id, work_id),
            ).rowcount
        if changed != 1:
            raise BrainIntakeError("candidate-rejection-race-lost")
        return {"ok": True, "status": "rejected", "rejected": 1}

    def supersede_memory(
        self,
        work_id: str,
        *,
        candidate_id: str,
        obsolete_memory_id: str,
        authorized_user: str,
    ) -> dict[str, Any]:
        self._authorize_actor(authorized_user, work_id)
        with self.connect() as db:
            candidate = db.execute(
                "SELECT * FROM candidates WHERE id=? AND work_id=?",
                (candidate_id, work_id),
            ).fetchone()
        if (
            not candidate
            or candidate["status"] not in {"pending", "eligible"}
            or candidate["eligibility_reason"] not in {
                "correction-requires-review", "conflict-requires-review",
            }
            or not candidate["registry_candidate_id"]
        ):
            raise BrainAuthorizationError("candidate-not-verified-correction")
        import argparse as _argparse
        import memory_registry
        registry_db = memory_registry.connect()
        try:
            registry_candidate = registry_db.execute(
                "SELECT injection_status,source_state FROM memory_candidates WHERE id=?",
                (candidate["registry_candidate_id"],),
            ).fetchone()
            if (
                not registry_candidate
                or registry_candidate["injection_status"] != "clear"
                or registry_candidate["source_state"] != "active"
            ):
                raise BrainAuthorizationError("candidate-not-verified-correction")
            registry_db.execute(
                "UPDATE memory_candidates SET governance_eligible=1 WHERE id=?",
                (candidate["registry_candidate_id"],),
            )
            registry_db.commit()
            try:
                approved = memory_registry.approve_candidate(
                    registry_db,
                    _argparse.Namespace(
                        id=str(candidate["registry_candidate_id"]),
                        reviewer="josh2",
                        supersedes=clean_text(obsolete_memory_id, 120),
                    ),
                )
            except SystemExit as exc:
                raise BrainIntakeError("candidate-supersession-rejected") from exc
        finally:
            registry_db.close()
        with self.connect() as db, self.transaction(db):
            current = db.execute(
                """SELECT phase,cancel_requested,user_cancel_requested,reference_only
                     FROM submissions WHERE work_id=?""",
                (work_id,),
            ).fetchone()
            if (
                not current
                or current["phase"] == "forgotten"
                or current["cancel_requested"]
                or current["user_cancel_requested"]
                or current["reference_only"]
            ):
                raise BrainIntakeError("source-not-candidate-eligible")
            db.execute(
                """UPDATE candidates SET status='active',eligibility_reason='approved-supersession',
                          registry_memory_id=? WHERE id=? AND work_id=?""",
                (str(approved["recordId"]), candidate_id, work_id),
            )
            db.execute(
                """UPDATE candidates SET status='superseded'
                     WHERE registry_memory_id=? AND id!=?""",
                (clean_text(obsolete_memory_id, 120), candidate_id),
            )
        return {"ok": True, "status": "active", "superseded": 1}

    def search_source(self, *, query: str, agent: str, limit: int = 6) -> dict[str, Any]:
        if agent not in AUTHORIZED_AGENTS:
            raise BrainAuthorizationError("unknown-agent")
        terms = [term for term in re.findall(r"[A-Za-z0-9_.-]{3,}", query)][:16]
        match = " OR ".join(f'"{term}"' for term in terms) if terms else '"__none__"'
        query_vector, query_norm = local_embedding(query)
        with self.connect() as db:
            try:
                lexical_rows = db.execute(
                    """SELECT chunk_id,bm25(source_chunk_fts) AS rank
                         FROM source_chunk_fts WHERE source_chunk_fts MATCH ?
                         ORDER BY rank LIMIT ?""",
                    (match, max(12, min(limit * 8, 100))),
                ).fetchall()
            except sqlite3.OperationalError:
                lexical_rows = []
            lexical = {str(row["chunk_id"]): float(row["rank"]) for row in lexical_rows}
            rows = db.execute(
                """SELECT c.*,v.vector_json,v.embedding_version,e.model_route,
                          s.privacy_class,s.current_owner,s.phase,s.cancel_requested
                     FROM source_chunks c JOIN source_vectors v ON v.chunk_id=c.id
                     JOIN extractions e ON e.id=c.extraction_id
                     JOIN submissions s ON s.work_id=c.work_id
                    WHERE s.phase!='forgotten' AND s.cancel_requested=0
                    ORDER BY c.created_at DESC,c.id LIMIT 2000""",
            ).fetchall()
        ranked: list[tuple[bool, float, sqlite3.Row]] = []
        for row in rows:
            if (
                row["privacy_class"] != "dashboard-safe"
                and agent not in {row["current_owner"], "joshex"}
            ):
                continue
            try:
                vector = json.loads(str(row["vector_json"]))
            except (json.JSONDecodeError, TypeError):
                continue
            semantic_score = cosine_similarity(query_vector, vector) if query_norm else 0.0
            is_lexical = str(row["id"]) in lexical
            if not is_lexical and semantic_score < SEMANTIC_RETRIEVAL_THRESHOLD:
                continue
            ranked.append((is_lexical, semantic_score, row))
        ranked.sort(key=lambda item: (-int(item[0]), -item[1], str(item[2]["id"])))
        visible = []
        for is_lexical, semantic_score, row in ranked[: max(1, min(limit, 30))]:
            visible.append({
                "resultType": "source_evidence",
                "sourceRef": stable_id("source-evidence", row["work_id"], length=28),
                "chunkRef": str(row["provenance_ref"]),
                "workId": str(row["work_id"]),
                "excerpt": clean_text(row["text_private"], 800),
                "confidence": float(row["confidence"]),
                "coverage": row["coverage"],
                "privacy": row["privacy_class"],
                "retrievalMode": "hybrid" if is_lexical and semantic_score else "lexical" if is_lexical else "semantic",
                "embeddingVersion": str(row["embedding_version"]),
            })
        return {"ok": True, "resultType": "source_evidence", "count": len(visible), "results": visible}

    def forget_preview(self, work_id: str, *, authorized_user: str) -> dict[str, Any]:
        normalized_user = self._authorize_actor(authorized_user, work_id)
        with self.connect() as db, self.transaction(db):
            submission = db.execute("SELECT * FROM submissions WHERE work_id=?", (work_id,)).fetchone()
            if not submission or submission["phase"] == "forgotten":
                raise BrainIntakeError("unknown-or-forgotten-work")
            impact = {
                "artifacts": int(db.execute("SELECT COUNT(*) FROM submission_artifacts WHERE work_id=?", (work_id,)).fetchone()[0]),
                "extractions": int(db.execute("SELECT COUNT(*) FROM extractions WHERE work_id=?", (work_id,)).fetchone()[0]),
                "candidates": int(db.execute("SELECT COUNT(*) FROM candidates WHERE work_id=?", (work_id,)).fetchone()[0]),
                "activeMemories": int(db.execute("SELECT COUNT(*) FROM candidates WHERE work_id=? AND registry_memory_id!=''", (work_id,)).fetchone()[0]),
            }
            token = secrets.token_urlsafe(28)
            expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=FORGET_TTL_SECONDS)
            db.execute(
                """INSERT INTO actions(
                     token_hash,work_id,authorized_user,action,impact_json,expires_at,consumed_at,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    hashlib.sha256(token.encode()).hexdigest(), work_id,
                    normalized_user, "forget-confirm", json.dumps(impact, sort_keys=True),
                    expires.replace(microsecond=0).isoformat().replace("+00:00", "Z"), None, utc_now(),
                ),
            )
        return {"ok": True, "confirmationRequired": any(impact.values()), "impact": impact, "confirmationToken": token}

    def forget(self, work_id: str, *, authorized_user: str, confirmation_token: str) -> dict[str, Any]:
        token_hash = hashlib.sha256(confirmation_token.encode()).hexdigest()
        normalized_user = self._authorize_actor(authorized_user, work_id)
        # Validate the exact source, action, user, expiry, and one-time token
        # before touching the governed registry.  The registry tombstone still
        # precedes local source deletion once authorization is established.
        with self.connect() as db:
            authorized_action = db.execute(
                """SELECT a.*,s.phase AS submission_phase FROM actions a
                   JOIN submissions s ON s.work_id=a.work_id WHERE a.token_hash=?""",
                (token_hash,),
            ).fetchone()
        if (
            not authorized_action
            or authorized_action["work_id"] != work_id
            or authorized_action["action"] != "forget-confirm"
        ):
            raise BrainAuthorizationError("forget-binding-mismatch")
        if authorized_action["submission_phase"] == "forgotten":
            raise BrainIntakeError("source-already-forgotten")
        if (
            authorized_action["consumed_at"]
            or parse_utc(authorized_action["expires_at"]) < dt.datetime.now(dt.timezone.utc)
        ):
            raise BrainAuthorizationError("forget-token-expired-or-used")
        if not hmac.compare_digest(str(authorized_action["authorized_user"]), normalized_user):
            raise BrainAuthorizationError("forget-user-mismatch")

        with self.connect() as db, self.transaction(db):
            marked = db.execute(
                """UPDATE submissions SET cancel_requested=1,updated_at=?
                   WHERE work_id=? AND phase!='forgotten'""",
                (utc_now(), work_id),
            ).rowcount
            if marked == 1:
                self._enqueue_ready(db, work_id, force_cancel=True)
        if marked != 1:
            raise BrainIntakeError("source-already-forgotten")

        paths_to_delete: list[tuple[Path, bool]] = []
        blob_delete_target = 0
        blob_deleted = 0
        # Tombstone governed memory first. If the local cleanup later needs a
        # retry, privacy fails closed because no active assertion remains.
        import argparse as _argparse
        import memory_registry
        registry_db = memory_registry.connect()
        try:
            registry_result = memory_registry.forget_source(
                registry_db,
                _argparse.Namespace(source=f"brain-source:{work_id}", actor="josh2", confirm=True),
            )
        finally:
            registry_db.close()
        with self.connect() as db, self.transaction(db):
            action = db.execute("SELECT * FROM actions WHERE token_hash=?", (token_hash,)).fetchone()
            if not action or action["work_id"] != work_id or action["action"] != "forget-confirm":
                raise BrainAuthorizationError("forget-binding-mismatch")
            if action["consumed_at"] or parse_utc(action["expires_at"]) < dt.datetime.now(dt.timezone.utc):
                raise BrainAuthorizationError("forget-token-expired-or-used")
            if not hmac.compare_digest(str(action["authorized_user"]), normalized_user):
                raise BrainAuthorizationError("forget-user-mismatch")
            changed = db.execute(
                "UPDATE actions SET consumed_at=? WHERE token_hash=? AND consumed_at IS NULL",
                (utc_now(), token_hash),
            ).rowcount
            if changed != 1:
                raise BrainAuthorizationError("forget-consume-race-lost")
            impact = json.loads(action["impact_json"])
            artifacts = db.execute(
                """SELECT a.digest,a.stored_path,a.ref_count,COUNT(*) AS work_ref_count
                   FROM submission_artifacts sa JOIN artifacts a ON a.digest=sa.digest
                   WHERE sa.work_id=? GROUP BY a.digest,a.stored_path,a.ref_count""",
                (work_id,),
            ).fetchall()
            delete_digests: list[str] = []
            for row in artifacts:
                remaining_refs = int(row["ref_count"]) - int(row["work_ref_count"])
                if remaining_refs <= 0:
                    paths_to_delete.append((Path(row["stored_path"]), True))
                    delete_digests.append(str(row["digest"]))
                    blob_delete_target += 1
                else:
                    db.execute(
                        "UPDATE artifacts SET ref_count=? WHERE digest=?",
                        (remaining_refs, row["digest"]),
                    )
            for row in db.execute("SELECT private_path FROM extractions WHERE work_id=?", (work_id,)).fetchall():
                if row["private_path"]:
                    paths_to_delete.append((Path(row["private_path"]), False))
            cleanup_sources = db.execute(
                """SELECT source_cleanup_path,source_cleanup_fingerprint
                     FROM attachment_intents
                     WHERE work_id=? AND source_cleanup_path!=''""",
                (work_id,),
            ).fetchall()
            for cleanup in cleanup_sources:
                cleanup_path = Path(str(cleanup["source_cleanup_path"]))
                if not cleanup_path.exists() and not cleanup_path.is_symlink():
                    continue
                self._remove_gateway_download(
                    cleanup_path,
                    expected_fingerprint=str(cleanup["source_cleanup_fingerprint"]),
                )
            for path, is_blob in paths_to_delete:
                try:
                    if path.is_symlink():
                        raise BrainSafetyError("forget-cleanup-path-invalid")
                    resolved = path.resolve()
                    if self.root not in resolved.parents:
                        raise BrainSafetyError("forget-cleanup-path-invalid")
                    if resolved.exists():
                        file_info = resolved.lstat()
                        if (
                            not stat.S_ISREG(file_info.st_mode)
                            or file_info.st_uid != os.getuid()
                            or file_info.st_nlink != 1
                        ):
                            raise BrainSafetyError("forget-cleanup-path-invalid")
                        resolved.unlink()
                    if resolved.exists():
                        raise BrainSafetyError("forget-cleanup-verification-failed")
                    if is_blob:
                        blob_deleted += 1
                except OSError as exc:
                    raise BrainSafetyError("forget-cleanup-failed") from exc
            db.execute(
                """DELETE FROM source_vectors WHERE chunk_id IN
                   (SELECT id FROM source_chunks WHERE work_id=?)""",
                (work_id,),
            )
            db.execute("DELETE FROM source_chunk_fts WHERE work_id=?", (work_id,))
            db.execute("DELETE FROM source_chunks WHERE work_id=?", (work_id,))
            db.execute("DELETE FROM source_fts WHERE work_id=?", (work_id,))
            db.execute("DELETE FROM extractions WHERE work_id=?", (work_id,))
            db.execute("DELETE FROM source_revision_events WHERE work_id=?", (work_id,))
            db.execute("DELETE FROM submission_artifacts WHERE work_id=?", (work_id,))
            for digest in delete_digests:
                db.execute("DELETE FROM artifacts WHERE digest=?", (digest,))
            db.execute(
                """UPDATE candidates SET status='forgotten',subject='',predicate='',value_private='',
                     provenance_ref='',registry_candidate_id='',registry_memory_id=''
                   WHERE work_id=?""",
                (work_id,),
            )
            for intent_row in db.execute(
                "SELECT id FROM attachment_intents WHERE work_id=?",
                (work_id,),
            ).fetchall():
                tombstone_hash = hashlib.sha256(
                    f"forgotten:{work_id}:{intent_row['id']}:{secrets.token_hex(16)}".encode()
                ).hexdigest()
                db.execute(
                    """UPDATE attachment_intents SET source_message_ref='',file_ref='',media_kind='forgotten',
                         declared_mime='',declared_size=0,token_hash=?,state='forgotten',failure_reason='',
                         source_cleanup_state='forgotten',source_cleanup_path='',source_cleanup_fingerprint='',
                         consumed_at=COALESCE(consumed_at,?) WHERE id=?""",
                    (tombstone_hash, utc_now(), intent_row["id"]),
                )
            db.execute(
                """UPDATE actions SET authorized_user='',impact_json='{}',
                     consumed_at=COALESCE(consumed_at,?) WHERE work_id=?""",
                (utc_now(), work_id),
            )
            db.execute(
                """UPDATE submissions SET phase='forgotten',caption_present=0,caption_private='',
                     objective_private='',media_group_ref='',source_private_json='{}',forgotten_at=?,updated_at=?
                   WHERE work_id=?""",
                (utc_now(), utc_now(), work_id),
            )
            db.execute(
                """UPDATE intake_jobs SET state='queued',stage='stored',attempt_count=0,
                          available_at=?,lease_owner='',lease_expires_at=NULL,
                          error_class='',completed_at=NULL,updated_at=?
                     WHERE work_id=? AND state!='completed'""",
                (utc_now(), utc_now(), work_id),
            )
            remaining_counts = {
                "sourceFts": int(db.execute(
                    "SELECT COUNT(*) FROM source_fts WHERE work_id=?", (work_id,),
                ).fetchone()[0]),
                "chunkFts": int(db.execute(
                    "SELECT COUNT(*) FROM source_chunk_fts WHERE work_id=?", (work_id,),
                ).fetchone()[0]),
                "chunks": int(db.execute(
                    "SELECT COUNT(*) FROM source_chunks WHERE work_id=?", (work_id,),
                ).fetchone()[0]),
                "vectors": int(db.execute(
                    """SELECT COUNT(*) FROM source_vectors WHERE chunk_id IN
                       (SELECT id FROM source_chunks WHERE work_id=?)""",
                    (work_id,),
                ).fetchone()[0]),
                "extractions": int(db.execute(
                    "SELECT COUNT(*) FROM extractions WHERE work_id=?", (work_id,),
                ).fetchone()[0]),
            }
            remaining = sum(remaining_counts.values())
            receipt_id = stable_id("brain-forget", work_id, utc_now(), length=28)
            db.execute(
                "INSERT INTO deletion_receipts VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    receipt_id, hashlib.sha256(work_id.encode()).hexdigest(), impact["artifacts"],
                    impact["extractions"], impact["candidates"], int(registry_result.get("recordCount") or 0),
                    blob_deleted, remaining, utc_now(),
                ),
            )
        return {
            "ok": remaining == 0, "forgotten": True,
            "artifactCount": impact["artifacts"], "extractionCount": impact["extractions"],
            "candidateCount": impact["candidates"], "memoryCount": int(registry_result.get("recordCount") or 0),
            "blobDeletedCount": blob_deleted, "blobDeleteTarget": blob_delete_target,
            "cleanupFailureCount": 0, "retrievalHitsAfter": remaining,
            "chunkIndexRemnants": remaining_counts["chunks"] + remaining_counts["chunkFts"],
            "vectorRemnants": remaining_counts["vectors"],
        }

    def final_receipt(self, work_id: str) -> dict[str, Any]:
        with self.connect() as db:
            submission = db.execute("SELECT * FROM submissions WHERE work_id=?", (work_id,)).fetchone()
            if not submission:
                raise BrainIntakeError("unknown-work")
            artifacts = db.execute(
                """SELECT a.digest,a.media_class,a.quarantine_reason,a.ref_count
                   FROM submission_artifacts sa JOIN artifacts a ON a.digest=sa.digest
                   WHERE sa.work_id=?""",
                (work_id,),
            ).fetchall()
            extractions = db.execute(
                "SELECT status,coverage,warnings_json,model_route FROM extractions WHERE work_id=?",
                (work_id,),
            ).fetchall()
            candidates = db.execute(
                "SELECT status,eligibility_reason,candidate_type FROM candidates WHERE work_id=?",
                (work_id,),
            ).fetchall()
            attachment_failures = db.execute(
                """SELECT failure_reason FROM attachment_intents
                     WHERE work_id=? AND consumed_at IS NOT NULL AND failure_reason!=''""",
                (work_id,),
            ).fetchall()
        failure_reasons = sorted({
            str(row["failure_reason"])
            for row in attachment_failures
            if str(row["failure_reason"]) in ATTACHMENT_FAILURE_REASONS
        })
        quarantined = (
            any(row["quarantine_reason"] for row in artifacts)
            or submission["phase"] == "quarantined"
        )
        local_digest_counts: dict[str, int] = {}
        digest_ref_counts: dict[str, int] = {}
        for row in artifacts:
            digest = str(row["digest"])
            local_digest_counts[digest] = local_digest_counts.get(digest, 0) + 1
            digest_ref_counts[digest] = int(row["ref_count"])
        duplicate_count = sum(count - 1 for count in local_digest_counts.values())
        duplicate_count += sum(
            1 for digest, count in local_digest_counts.items()
            if digest_ref_counts[digest] > count
        )
        active = sum(row["status"] == "active" for row in candidates)
        pending = sum(row["status"] in {"pending", "eligible"} for row in candidates)
        active_types = sorted({
            str(row["candidate_type"])
            for row in candidates
            if row["status"] == "active" and str(row["candidate_type"]) in AUTO_ELIGIBLE_TYPES
        })
        pending_reasons = sorted({
            str(row["eligibility_reason"])
            if str(row["eligibility_reason"]) in SAFE_REVIEW_REASONS
            else "manual-review-required"
            for row in candidates if row["status"] in {"pending", "eligible"}
        })
        unsupported = sorted({
            warning
            for row in extractions if row["status"] == "unsupported"
            for warning in json.loads(row["warnings_json"])
        } | set(failure_reasons))
        return {
            "Stored": "Quarantined" if quarantined else "Yes" if artifacts else "No",
            "Extracted": {
                "types": sorted({row["media_class"] for row in artifacts}) or ["n/a"],
                "coverage": sorted({str(row["coverage"]) for row in extractions if row["coverage"]}) or ["none"],
                "routes": sorted({str(row["model_route"]) for row in extractions if row["model_route"]}) or ["local-none"],
            },
            "Learned": {"count": active, "types": active_types or ["n/a"]},
            "Source indexed": "Yes" if any(row["status"] == "indexed" for row in extractions) else "No",
            "Pending review": {"count": pending, "reasons": pending_reasons or ["n/a"]},
            "Duplicates": duplicate_count or "n/a",
            "Unsupported": unsupported or ["n/a"],
            "Privacy": submission["privacy_class"],
            "Retention": "not retained" if submission["phase"] == "forgotten" else "privately retained",
            "How to correct": "Reply to this receipt with a correction.",
            "How to forget": "Reply to this receipt with Forget, then confirm the impact preview.",
            "Approval needed": "memory review" if pending else "n/a",
        }

    def status(self) -> dict[str, Any]:
        with self.connect() as db:
            phases = {row["phase"]: int(row["count"]) for row in db.execute("SELECT phase,COUNT(*) AS count FROM submissions GROUP BY phase")}
            pending = int(db.execute("SELECT COUNT(*) FROM candidates WHERE status IN ('pending','eligible')").fetchone()[0])
            quarantined = int(db.execute("SELECT COUNT(*) FROM submissions WHERE phase='quarantined'").fetchone()[0])
            forget_pending = int(db.execute(
                """SELECT COUNT(DISTINCT s.work_id)
                     FROM submissions s
                     JOIN actions a ON a.work_id=s.work_id
                    WHERE s.cancel_requested=1 AND s.phase!='forgotten'
                      AND a.action='forget-confirm' AND a.consumed_at IS NULL"""
            ).fetchone()[0])
            jobs = {
                str(row["state"]): int(row["count"])
                for row in db.execute("SELECT state,COUNT(*) AS count FROM intake_jobs GROUP BY state")
            }
            results = int(db.execute("SELECT COUNT(*) FROM intake_results").fetchone()[0])
        return {
            "ok": quarantined == 0 and forget_pending == 0,
            "schemaVersion": SCHEMA_VERSION,
            "lifecycleVersion": LIFECYCLE_VERSION,
            "extractionVersion": EXTRACTION_VERSION,
            "phases": phases,
            "pendingReview": pending,
            "quarantined": quarantined,
            "forgetCleanupPending": forget_pending,
            "worker": {"jobs": jobs, "terminalResults": results},
            "privacy": {"rawContentIncluded": False, "identifiersIncluded": False, "countsOnly": True},
        }


HOOK_MARKER = "// JCU10_BRAIN_GOVERNED_QUEUE_V5"


def hook_source() -> str:
    return r'''
// JCU10_BRAIN_GOVERNED_QUEUE_V5
function jcu10BrainAttachment(message) {
	const candidates = [
		["photo", message.photo?.at?.(-1)], ["video", message.video], ["video_note", message.video_note],
		["audio", message.audio], ["voice", message.voice], ["document", message.document],
		["animation", message.animation], ["sticker", message.sticker]
	];
	for (const [kind, value] of candidates) if (value?.file_id) return {
		kind, fileId: value.file_id, sourceMessageId: String(message.message_id),
		mime: value.mime_type ?? "", size: Number(value.file_size ?? 0)
	};
	return null;
}
function jcu10BrainHook(command, payload) {
	const executable = process.env.BRAIN_MEDIA_INTAKE_PYTHON || "/opt/homebrew/bin/python3";
	const script = process.env.BRAIN_MEDIA_INTAKE_SCRIPT || "/Users/josh2.0/.openclaw/workspace/mission-control/scripts/brain_media_intake.py";
	const result = spawnSync(executable, [script, command, "--private-stdin"], {
		input: JSON.stringify(payload), encoding: "utf8", timeout: 120000,
		env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" }
	});
	if (result.status !== 0) throw new Error(`Brain intake ${command} failed closed`);
	const parsed = JSON.parse(result.stdout || "{}");
	if (parsed.ok !== true && !(command === "postdownload" && parsed.quarantined === true)) {
		throw new Error(`Brain intake ${command} was not accepted`);
	}
	return parsed;
}
function jcu10BrainActionCheck(message, edited = false) {
	const executable = process.env.BRAIN_GATEWAY_ACTIONS_PYTHON || "/opt/homebrew/bin/python3";
	const script = process.env.BRAIN_GATEWAY_ACTIONS_SCRIPT || "/Users/josh2.0/.openclaw/workspace/mission-control/scripts/brain_gateway_actions.py";
	const payload = {
		chatId: String(message.chat.id), threadId: String(message.message_thread_id ?? ""),
		messageId: String(message.message_id), senderId: String(message.from?.id ?? ""),
		senderIsBot: Boolean(message.from?.is_bot ?? false),
		replyToMessageId: String(message.reply_to_message?.message_id ?? ""),
		text: message.text ?? message.caption ?? "", edited: Boolean(edited)
	};
	const result = spawnSync(executable, [script, "process-event", "--private-stdin"], {
		input: JSON.stringify(payload), encoding: "utf8", timeout: 120000,
		env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" }
	});
	if (result.status !== 0) throw new Error("Brain action check failed closed");
	const parsed = JSON.parse(result.stdout || "{}");
	if (parsed.ok !== true && parsed.handled !== true) throw new Error("Brain action was not fenced");
	return parsed;
}
function jcu10BrainRouteCheck(messages) {
	const primary = messages[0];
	if (!primary) return { brain: false, handled: false, silentDrop: false };
	return jcu10BrainHook("route-check", {
		chatId: String(primary.chat.id), threadId: String(primary.message_thread_id ?? ""),
		senderId: String(primary.from?.id ?? ""),
		senderIsBot: Boolean(primary.from?.is_bot ?? false)
	});
}
function jcu10BrainPredownload(messages, route, edited = false) {
	if (route?.brain !== true || route?.handled === true) return { brain: false };
	const primary = messages.find((message) => message.caption || message.text) ?? messages[0];
	if (!primary) return { brain: false };
	const attachments = messages.map(jcu10BrainAttachment).filter(Boolean);
	if (attachments.length === 0) return { brain: false };
	return jcu10BrainHook("predownload", {
		chatId: String(primary.chat.id), threadId: String(primary.message_thread_id ?? ""),
		messageId: String(primary.message_id), senderId: String(primary.from?.id ?? ""),
		senderIsBot: Boolean(primary.from?.is_bot ?? false),
		edited: Boolean(edited),
		mediaGroupId: String(primary.media_group_id ?? ""), caption: primary.caption ?? "",
		attachments
	});
}
function jcu10BrainPostdownload(receipt, message, media) {
	if (!receipt?.brain) return;
	if (!media?.path) return jcu10BrainAttachmentFailure(receipt, message, "download-unavailable");
	const sourceMessageId = String(message.message_id);
	const token = receipt.downloadTokens?.find((item) => item.sourceMessageId === sourceMessageId);
	if (!token) throw new Error("Brain intake download capability missing");
	if (token.consumed === true) return;
	return jcu10BrainHook("postdownload", {
		workId: receipt.workId, attachmentId: token.attachmentId, token: token.token, path: media.path
	});
}
function jcu10BrainAttachmentFailure(receipt, message, reason) {
	if (!receipt?.brain) return;
	const sourceMessageId = String(message.message_id);
	const token = receipt.downloadTokens?.find((item) => item.sourceMessageId === sourceMessageId);
	if (!token) throw new Error("Brain intake download capability missing");
	if (token.consumed === true) return { ok: true, duplicate: true, queued: receipt.queued === true };
	return jcu10BrainHook("attachment-failure", {
		workId: receipt.workId, attachmentId: token.attachmentId, token: token.token, reason
	});
}
'''.strip("\n")


def _valid_installed_hook(content: str) -> bool:
    expected_once = (
        HOOK_MARKER,
        'import { spawnSync } from "node:child_process";',
        "function jcu10BrainAttachment(",
        "const jcu10BrainAdopt = async (",
        "function jcu10BrainHook(",
        "function jcu10BrainActionCheck(",
        "function jcu10BrainRouteCheck(",
        "function jcu10BrainPredownload(",
        "function jcu10BrainPostdownload(",
        "function jcu10BrainAttachmentFailure(",
        "entry.messages.map((item) => item.msg), entry.brainRoute",
        "jcu10BrainPredownload([msg], brainRoute)",
    )
    return (
        all(content.count(fragment) == 1 for fragment in expected_once)
        and content.count("jcu10BrainPostdownload(jcu10BrainReceipt") == 2
        and content.count("Brain intake was not durably queued") == 3
        and content.count(
            "await commitDispatchDedupeKeys(keys, { requirePersistent: true });"
        ) == 1
        and content.count("await jcu10BrainAdopt(") == 10
        and content.count("recordTelegramMessageProcessingResult({ kind: \"completed\" });") == 8
        and content.count("beginSpooledReplaySettlementHolds(participants)") == 1
        and content.count("jcu10BrainActionCheck(") == 3
        and content.count("const jcu10BrainRoute = hasInboundMedia(event.msg)") == 1
        and content.count("brainRoute: jcu10BrainRoute") == 1
    )


def patch_openclaw_ingress(path: Path, *, install: bool, backup_root: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise BrainConfigurationError("openclaw-ingress-symlink")
    content = path.read_text()
    current_hash = hashlib.sha256(content.encode()).hexdigest()
    if backup_root.is_symlink():
        raise BrainConfigurationError("openclaw-hook-backup-root-symlink")
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_root, 0o700)
    backup = backup_root / f"{OPENCLAW_VERSION}-{OPENCLAW_INGRESS_ORIGINAL_SHA256}.js"
    manifest = backup_root / "manifest.json"
    if not install:
        if not backup.exists():
            raise BrainConfigurationError("openclaw-hook-backup-missing")
        if backup.is_symlink() or sha256_file(backup) != OPENCLAW_INGRESS_ORIGINAL_SHA256:
            raise BrainConfigurationError("openclaw-hook-backup-hash-invalid")
        if current_hash != OPENCLAW_INGRESS_ORIGINAL_SHA256:
            try:
                rollback_manifest = load_json(manifest)
            except BrainConfigurationError as exc:
                raise BrainConfigurationError("openclaw-hook-rollback-target-drift") from exc
            if (
                not _valid_installed_hook(content)
                or rollback_manifest.get("state") != "installed"
                or rollback_manifest.get("patchVersion") != OPENCLAW_HOOK_PATCH_VERSION
                or rollback_manifest.get("patchedHash") != current_hash
            ):
                raise BrainConfigurationError("openclaw-hook-rollback-target-drift")
        atomic_write(path, backup.read_bytes(), 0o644)
        if sha256_file(path) != OPENCLAW_INGRESS_ORIGINAL_SHA256:
            raise BrainConfigurationError("openclaw-hook-rollback-verification-failed")
        atomic_write(manifest, (json.dumps({"state": "rolled-back", "version": OPENCLAW_VERSION, "at": utc_now()}, indent=2) + "\n").encode())
        return {"ok": True, "installed": False, "rolledBack": True}
    if HOOK_MARKER in content:
        try:
            installed_manifest = load_json(manifest)
        except BrainConfigurationError as exc:
            raise BrainConfigurationError("openclaw-hook-installed-content-invalid") from exc
        if (
            not _valid_installed_hook(content)
            or installed_manifest.get("state") != "installed"
            or installed_manifest.get("version") != OPENCLAW_VERSION
            or installed_manifest.get("patchVersion") != OPENCLAW_HOOK_PATCH_VERSION
            or installed_manifest.get("originalHash") != OPENCLAW_INGRESS_ORIGINAL_SHA256
            or installed_manifest.get("patchedHash") != current_hash
            or not backup.exists()
            or backup.is_symlink()
            or sha256_file(backup) != OPENCLAW_INGRESS_ORIGINAL_SHA256
        ):
            raise BrainConfigurationError("openclaw-hook-installed-content-invalid")
        return {"ok": True, "installed": True, "duplicate": True, "version": OPENCLAW_VERSION}
    if current_hash != OPENCLAW_INGRESS_ORIGINAL_SHA256:
        raise BrainConfigurationError("openclaw-ingress-version-or-hash-drift")
    atomic_write(backup, content.encode(), 0o600)
    import_anchor = 'import { AsyncLocalStorage } from "node:async_hooks";'
    if import_anchor not in content:
        raise BrainConfigurationError("openclaw-hook-import-anchor-missing")
    content = content.replace(import_anchor, import_anchor + '\nimport { spawnSync } from "node:child_process";', 1)
    region_anchor = "//#region extensions/telegram/src/bot-processing-outcome.ts"
    if region_anchor not in content:
        raise BrainConfigurationError("openclaw-hook-region-anchor-missing")
    content = content.replace(region_anchor, hook_source() + "\n" + region_anchor, 1)
    adoption_anchor = "\tconst createSpooledReplayParticipantForBufferedWork ="
    if content.count(adoption_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-adoption-anchor-ambiguous")
    content = content.replace(
        adoption_anchor,
        "\tconst jcu10BrainAdopt = async (keys, participants = []) => {\n"
        "\t\tconst releaseSettlementHolds = beginSpooledReplaySettlementHolds(participants);\n"
        "\t\ttry {\n"
        "\t\t\tawait commitDispatchDedupeKeys(keys, { requirePersistent: true });\n"
        "\t\t} catch (error) {\n"
        "\t\t\treleaseSettlementHolds(\"replay-pending\");\n"
        "\t\t\tthrow error;\n"
        "\t\t}\n"
        "\t\treleaseSettlementHolds(\"discard-pending\");\n"
        "\t\tsettleSpooledReplayParticipants(participants, { kind: \"completed\" });\n"
        "\t};\n"
        + adoption_anchor,
        1,
    )
    cache_anchor = "\t\t\tawait recordMessageForReplyChain(event.msg, resolvedThreadId ?? dmThreadId);"
    if content.count(cache_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-reply-cache-anchor-ambiguous")
    content = content.replace(
        cache_anchor,
        "\t\t\tconst jcu10BrainAction = jcu10BrainActionCheck(event.msg);\n"
        "\t\t\tif (jcu10BrainAction?.handled === true) {\n"
        "\t\t\t\tawait jcu10BrainAdopt(dispatchDedupeKeys);\n"
        "\t\t\t\trecordTelegramMessageProcessingResult({ kind: \"completed\" });\n"
        "\t\t\t\treturn;\n"
        "\t\t\t}\n"
        "\t\t\tconst jcu10BrainRoute = hasInboundMedia(event.msg)\n"
        "\t\t\t\t? jcu10BrainRouteCheck([event.msg])\n"
        "\t\t\t\t: { brain: false, handled: false, silentDrop: false };\n"
        "\t\t\tif (jcu10BrainRoute?.handled === true && jcu10BrainRoute?.silentDrop === true) {\n"
        "\t\t\t\tawait jcu10BrainAdopt(dispatchDedupeKeys);\n"
        "\t\t\t\trecordTelegramMessageProcessingResult({ kind: \"completed\" });\n"
        "\t\t\t\treturn;\n"
        "\t\t\t}\n"
        "\t\t\tif (event.brainEditOnly === true) {\n"
        "\t\t\t\tif (jcu10BrainRoute?.brain === true) {\n"
        "\t\t\t\t\tconst editedReceipt = jcu10BrainPredownload([event.msg], jcu10BrainRoute, true);\n"
        "\t\t\t\t\tif (editedReceipt?.brain !== true) throw new Error(\"Brain edit was not durably recorded\");\n"
        "\t\t\t\t\tawait jcu10BrainAdopt(dispatchDedupeKeys);\n"
        "\t\t\t\t\trecordTelegramMessageProcessingResult({ kind: \"completed\" });\n"
        "\t\t\t\t\treturn;\n"
        "\t\t\t\t}\n"
        "\t\t\t\treleaseDispatchDedupeKeys(dispatchDedupeKeys);\n"
        "\t\t\t\treturn;\n"
        "\t\t\t}\n"
        "\t\t\tif (jcu10BrainRoute?.brain !== true) {\n"
        "\t\t\t\tawait recordMessageForReplyChain(event.msg, resolvedThreadId ?? dmThreadId);\n"
        "\t\t\t}",
        1,
    )
    process_params_anchor = "\t\t\t\ttopicConfig,\n\t\t\t\tsendOversizeWarning: event.sendOversizeWarning,"
    if content.count(process_params_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-route-propagation-anchor-missing")
    content = content.replace(
        process_params_anchor,
        "\t\t\t\ttopicConfig,\n"
        "\t\t\t\tbrainRoute: jcu10BrainRoute,\n"
        "\t\t\t\tsendOversizeWarning: event.sendOversizeWarning,",
        1,
    )
    destructure_anchor = (
        "const { ctx, msg, chatId, isGroup, isForum, resolvedThreadId, dmThreadId, dmPolicy, "
        "storeAllowFrom, senderId, effectiveGroupAllow, effectiveDmAllow, groupConfig, topicConfig, "
        "sendOversizeWarning"
    )
    if content.count(destructure_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-route-destructure-anchor-missing")
    content = content.replace(
        destructure_anchor,
        "const { ctx, msg, chatId, isGroup, isForum, resolvedThreadId, dmThreadId, dmPolicy, "
        "storeAllowFrom, senderId, effectiveGroupAllow, effectiveDmAllow, groupConfig, topicConfig, "
        "brainRoute, sendOversizeWarning",
        1,
    )
    group_entry_anchor = "\t\t\t\t\ttopicConfig,\n\t\t\t\t\tdispatchDedupeKeys,"
    if content.count(group_entry_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-group-route-anchor-missing")
    content = content.replace(
        group_entry_anchor,
        "\t\t\t\t\ttopicConfig,\n\t\t\t\t\tbrainRoute,\n\t\t\t\t\tdispatchDedupeKeys,",
        1,
    )
    group_skip_anchor = "\t\t\tif (await shouldSkipMediaDownloadForUnaddressedMentionGroup({"
    if content.count(group_skip_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-group-skip-anchor-ambiguous")
    content = content.replace(
        group_skip_anchor,
        "\t\t\tconst jcu10BrainReceipt = jcu10BrainPredownload(\n"
        "\t\t\t\tentry.messages.map((item) => item.msg), entry.brainRoute\n"
        "\t\t\t);\n"
        "\t\t\tlet jcu10BrainQueued = jcu10BrainReceipt?.queued === true;\n"
        "\t\t\tif (jcu10BrainReceipt?.brain === true && jcu10BrainQueued) {\n"
        "\t\t\t\tawait jcu10BrainAdopt(entry.dispatchDedupeKeys, entry.spooledReplayParticipants);\n"
        "\t\t\t\treturn;\n"
        "\t\t\t}\n"
        "\t\t\tif (jcu10BrainReceipt?.brain !== true && await shouldSkipMediaDownloadForUnaddressedMentionGroup({",
        1,
    )
    group_anchor = "\t\t\tconst allMedia = [];"
    if content.count(group_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-group-anchor-ambiguous")
    content = content.replace(
        group_anchor,
        group_anchor,
        1,
    )
    group_loop_anchor = (
        "\t\t\t\tconst sourceMessageId = String(msg.message_id);\n"
        "\t\t\t\tlet media;"
    )
    if content.count(group_loop_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-group-loop-anchor-ambiguous")
    content = content.replace(
        group_loop_anchor,
        "\t\t\t\tconst sourceMessageId = String(msg.message_id);\n"
        "\t\t\t\tconst jcu10BrainToken = jcu10BrainReceipt?.downloadTokens?.find(\n"
        "\t\t\t\t\t(item) => item.sourceMessageId === sourceMessageId\n"
        "\t\t\t\t);\n"
        "\t\t\t\tif (jcu10BrainReceipt?.brain === true && jcu10BrainToken?.consumed === true) continue;\n"
        "\t\t\t\tlet media;",
        1,
    )
    group_catch = "\t\t\t\t} catch (mediaErr) {\n\t\t\t\t\tif (!isRecoverableMediaGroupError(mediaErr)) throw mediaErr;"
    if content.count(group_catch) != 1:
        raise BrainConfigurationError("openclaw-hook-group-catch-anchor-ambiguous")
    content = content.replace(
        group_catch,
        "\t\t\t\t} catch (mediaErr) {\n"
        "\t\t\t\t\tif (jcu10BrainReceipt?.brain === true) {\n"
        "\t\t\t\t\t\tconst jcu10BrainFailed = jcu10BrainAttachmentFailure(\n"
        "\t\t\t\t\t\t\tjcu10BrainReceipt, msg,\n"
        "\t\t\t\t\t\t\tisMediaSizeLimitError(mediaErr) ? \"oversize\" : \"download-unavailable\"\n"
        "\t\t\t\t\t\t);\n"
        "\t\t\t\t\t\tjcu10BrainQueued ||= jcu10BrainFailed?.queued === true;\n"
        "\t\t\t\t\t\tcontinue;\n"
        "\t\t\t\t\t}\n"
        "\t\t\t\t\tif (!isRecoverableMediaGroupError(mediaErr)) throw mediaErr;",
        1,
    )
    group_post = "\t\t\t\tif (media) {\n\t\t\t\t\tallMedia.push({"
    if content.count(group_post) != 1:
        raise BrainConfigurationError("openclaw-hook-group-post-anchor-ambiguous")
    content = content.replace(
        group_post,
        "\t\t\t\tif (media) {\n"
        "\t\t\t\t\tlet jcu10BrainStored;\n"
        "\t\t\t\t\ttry {\n"
        "\t\t\t\t\t\tjcu10BrainStored = jcu10BrainPostdownload(jcu10BrainReceipt, msg, media);\n"
        "\t\t\t\t\t} catch {\n"
        "\t\t\t\t\t\tjcu10BrainStored = jcu10BrainAttachmentFailure(jcu10BrainReceipt, msg, \"corrupt\");\n"
        "\t\t\t\t\t}\n"
        "\t\t\t\t\tif (jcu10BrainReceipt?.brain === true) {\n"
        "\t\t\t\t\t\tjcu10BrainQueued ||= jcu10BrainStored?.queued === true;\n"
        "\t\t\t\t\t\tcontinue;\n"
        "\t\t\t\t\t}\n"
        "\t\t\t\t\tallMedia.push({",
        1,
    )
    group_empty_media = (
        "\t\t\t\t} else {\n"
        "\t\t\t\t\tpromptContextMessageSelection.set(sourceMessageId, \"exclude\");\n"
        "\t\t\t\t\tskippedCount++;\n"
        "\t\t\t\t}"
    )
    if content.count(group_empty_media) != 1:
        raise BrainConfigurationError("openclaw-hook-group-empty-media-anchor-ambiguous")
    content = content.replace(
        group_empty_media,
        "\t\t\t\t} else {\n"
        "\t\t\t\t\tif (jcu10BrainReceipt?.brain === true) {\n"
        "\t\t\t\t\t\tconst jcu10BrainFailed = jcu10BrainAttachmentFailure(\n"
        "\t\t\t\t\t\t\tjcu10BrainReceipt, msg, \"download-unavailable\"\n"
        "\t\t\t\t\t\t);\n"
        "\t\t\t\t\t\tjcu10BrainQueued ||= jcu10BrainFailed?.queued === true;\n"
        "\t\t\t\t\t\tcontinue;\n"
        "\t\t\t\t\t}\n"
        "\t\t\t\t\tpromptContextMessageSelection.set(sourceMessageId, \"exclude\");\n"
        "\t\t\t\t\tskippedCount++;\n"
        "\t\t\t\t}",
        1,
    )
    group_dispatch_anchor = "\t\t\tif (skippedCount > 0) {"
    if content.count(group_dispatch_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-group-dispatch-anchor-ambiguous")
    content = content.replace(
        group_dispatch_anchor,
        "\t\t\tif (jcu10BrainReceipt?.brain === true) {\n"
        "\t\t\t\tif (!jcu10BrainQueued) throw new Error(\"Brain intake was not durably queued\");\n"
        "\t\t\t\tawait jcu10BrainAdopt(entry.dispatchDedupeKeys, entry.spooledReplayParticipants);\n"
        "\t\t\t\treturn;\n"
        "\t\t\t}\n"
        + group_dispatch_anchor,
        1,
    )
    single_skip_anchor = "\t\tif (await shouldSkipMediaDownloadForUnaddressedMentionGroup({"
    if content.count(single_skip_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-single-skip-anchor-ambiguous")
    content = content.replace(
        single_skip_anchor,
        "\t\tconst jcu10BrainReceipt = jcu10BrainPredownload([msg], brainRoute);\n"
        "\t\tif (jcu10BrainReceipt?.brain === true && jcu10BrainReceipt.queued === true) {\n"
        "\t\t\tawait jcu10BrainAdopt(dispatchDedupeKeys);\n"
        "\t\t\trecordTelegramMessageProcessingResult({ kind: \"completed\" });\n"
        "\t\t\treturn;\n"
        "\t\t}\n"
        "\t\tif (jcu10BrainReceipt?.brain !== true && await shouldSkipMediaDownloadForUnaddressedMentionGroup({",
        1,
    )
    single_anchor = "\t\tlet media;\n\t\ttry {\n\t\t\tmedia = await resolveMedia({"
    if content.count(single_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-single-anchor-ambiguous")
    content = content.replace(
        single_anchor,
        single_anchor,
        1,
    )
    single_catch = "\t\t} catch (mediaErr) {\n\t\t\tif (isMediaSizeLimitError(mediaErr)) {"
    if content.count(single_catch) != 1:
        raise BrainConfigurationError("openclaw-hook-single-catch-anchor-ambiguous")
    content = content.replace(
        single_catch,
        "\t\t} catch (mediaErr) {\n"
        "\t\t\tif (jcu10BrainReceipt?.brain === true) {\n"
        "\t\t\t\tconst jcu10BrainFailed = jcu10BrainAttachmentFailure(\n"
        "\t\t\t\t\tjcu10BrainReceipt, msg,\n"
        "\t\t\t\t\tisMediaSizeLimitError(mediaErr) ? \"oversize\" : \"download-unavailable\"\n"
        "\t\t\t\t);\n"
        "\t\t\t\tif (!(jcu10BrainReceipt.queued === true || jcu10BrainFailed?.queued === true)) {\n"
        "\t\t\t\t\tthrow new Error(\"Brain intake was not durably queued\");\n"
        "\t\t\t\t}\n"
        "\t\t\t\tawait jcu10BrainAdopt(dispatchDedupeKeys);\n"
        "\t\t\t\trecordTelegramMessageProcessingResult({ kind: \"completed\" });\n"
        "\t\t\t\treturn;\n"
        "\t\t\t}\n"
        "\t\t\tif (isMediaSizeLimitError(mediaErr)) {",
        1,
    )
    single_post_anchor = "\t\tconst hasText = Boolean(getTelegramTextParts(msg).text.trim());"
    if content.count(single_post_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-single-post-anchor-ambiguous")
    content = content.replace(
        single_post_anchor,
        "\t\tlet jcu10BrainStored;\n"
        "\t\ttry {\n"
        "\t\t\tjcu10BrainStored = jcu10BrainPostdownload(jcu10BrainReceipt, msg, media);\n"
        "\t\t} catch {\n"
        "\t\t\tjcu10BrainStored = jcu10BrainAttachmentFailure(jcu10BrainReceipt, msg, \"corrupt\");\n"
        "\t\t}\n"
        "\t\tif (jcu10BrainReceipt?.brain === true) {\n"
        "\t\t\tif (!(jcu10BrainReceipt.queued === true || jcu10BrainStored?.queued === true)) {\n"
        "\t\t\t\tthrow new Error(\"Brain intake was not durably queued\");\n"
        "\t\t\t}\n"
        "\t\t\tawait jcu10BrainAdopt(dispatchDedupeKeys);\n"
        "\t\t\trecordTelegramMessageProcessingResult({ kind: \"completed\" });\n"
        "\t\t\treturn;\n"
        "\t\t}\n"
        + single_post_anchor,
        1,
    )
    edited_anchor = '''\tbot.on("edited_message", async (ctx) => {
\t\tconst msg = ctx.editedMessage;
\t\tif (!msg) return;
\t\tawait recordEditedMessageForReplyChain({
\t\t\tctxForDedupe: ctx,
\t\t\tmsg,
\t\t\trequireConfiguredGroup: false
\t\t});
\t});'''
    if content.count(edited_anchor) != 1:
        raise BrainConfigurationError("openclaw-hook-edited-message-anchor-missing")
    edited_replacement = '''\tbot.on("edited_message", async (ctx) => {
\t\tconst msg = ctx.editedMessage;
\t\tif (!msg) return;
\t\tconst editedAction = jcu10BrainActionCheck(msg, true);
\t\tif (editedAction?.handled === true) {
\t\t\tconst actionDedupe = await claimMessageDispatchDedupe(msg);
\t\t\tif (actionDedupe.process) {
\t\t\t\tawait jcu10BrainAdopt(actionDedupe.keys);
\t\t\t\trecordTelegramMessageProcessingResult({ kind: "completed" });
\t\t\t}
\t\t\treturn;
\t\t}
\t\tconst editedRoute = hasInboundMedia(msg)
\t\t\t? jcu10BrainRouteCheck([msg])
\t\t\t: { brain: false, handled: false, silentDrop: false };
\t\tif (editedRoute?.brain !== true) {
\t\t\tawait recordEditedMessageForReplyChain({
\t\t\t\tctxForDedupe: ctx,
\t\t\t\tmsg,
\t\t\t\trequireConfiguredGroup: false
\t\t\t});
\t\t\treturn;
\t\t}
\t\tif (editedRoute?.handled === true && editedRoute?.silentDrop === true) {
\t\t\tconst deniedDedupe = await claimMessageDispatchDedupe(msg);
\t\t\tif (deniedDedupe.process) {
\t\t\t\tawait jcu10BrainAdopt(deniedDedupe.keys);
\t\t\t\trecordTelegramMessageProcessingResult({ kind: "completed" });
\t\t\t}
\t\t\treturn;
\t\t}
\t\tconst isGroup = msg.chat.type === "group" || msg.chat.type === "supergroup";
\t\tconst isForum = await resolveTelegramForumFlag({
\t\t\tchatId: msg.chat.id,
\t\t\tchatType: msg.chat.type,
\t\t\tisGroup,
\t\t\tisForum: msg.chat.is_forum,
\t\t\tisTopicMessage: msg.is_topic_message,
\t\t\tgetChat
\t\t});
\t\tconst normalizedMsg = withResolvedTelegramForumFlag(msg, isForum);
\t\tawait handleInboundMessageLike({
\t\t\tctxForDedupe: ctx,
\t\t\tctx: buildSyntheticContext(ctx, normalizedMsg),
\t\t\tmsg: normalizedMsg,
\t\t\tchatId: normalizedMsg.chat.id,
\t\t\tisGroup,
\t\t\tisForum,
\t\t\tmessageThreadId: normalizedMsg.message_thread_id,
\t\t\tsenderId: normalizedMsg.from?.id != null ? String(normalizedMsg.from.id) : "",
\t\t\tsenderUsername: normalizedMsg.from?.username ?? "",
\t\t\trequireConfiguredGroup: false,
\t\t\tsendOversizeWarning: false,
\t\t\toversizeLogMessage: "brain edit media exceeds size limit",
\t\t\terrorMessage: "brain edit handler failed",
\t\t\tbrainEditOnly: true
\t\t});
\t});'''
    content = content.replace(edited_anchor, edited_replacement, 1)
    if not _valid_installed_hook(content):
        raise BrainConfigurationError("openclaw-hook-generated-content-invalid")
    node = shutil.which("node")
    if not node:
        raise BrainConfigurationError("openclaw-hook-node-validator-missing")
    syntax_fd, syntax_name = tempfile.mkstemp(prefix="brain-hook-check-", suffix=".mjs", dir=backup_root)
    try:
        os.fchmod(syntax_fd, 0o600)
        with os.fdopen(syntax_fd, "w", encoding="utf-8") as syntax_file:
            syntax_file.write(content)
            syntax_file.flush()
            os.fsync(syntax_file.fileno())
        syntax_fd = -1
        syntax_check = subprocess.run(
            [node, "--check", syntax_name],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if syntax_check.returncode != 0:
            raise BrainConfigurationError("openclaw-hook-generated-syntax-invalid")
    except subprocess.TimeoutExpired as exc:
        raise BrainConfigurationError("openclaw-hook-syntax-check-timeout") from exc
    finally:
        if syntax_fd >= 0:
            os.close(syntax_fd)
        Path(syntax_name).unlink(missing_ok=True)
    atomic_write(path, content.encode(), 0o644)
    patched_hash = hashlib.sha256(content.encode()).hexdigest()
    atomic_write(manifest, (json.dumps({
        "state": "installed", "version": OPENCLAW_VERSION,
        "patchVersion": OPENCLAW_HOOK_PATCH_VERSION,
        "originalHash": OPENCLAW_INGRESS_ORIGINAL_SHA256, "patchedHash": patched_hash,
        "installedAt": utc_now(),
    }, indent=2) + "\n").encode(), 0o600)
    return {"ok": True, "installed": True, "duplicate": False, "version": OPENCLAW_VERSION, "hashVerified": True}


def ensure_brain_lifecycle(
    store: BrainStore,
    work_id: str,
    *,
    lifecycle_root: Path,
    rollout_path: Path,
) -> dict[str, Any]:
    """Start Tier 3 while intake authority is live, then persist its opaque map."""
    from telegram_gateway_lifecycle import GatewayLifecycle, RolloutPolicy

    rollout = RolloutPolicy.load(rollout_path)
    if not rollout.brain_enabled("josh2"):
        raise BrainConfigurationError("brain-lifecycle-writer-disabled")
    gateway = GatewayLifecycle(lifecycle_root, rollout=rollout, owner="josh2")
    with store.connect() as db:
        submission = db.execute(
            "SELECT source_revision,source_private_json FROM submissions WHERE work_id=?", (work_id,),
        ).fetchone()
        latest_revision = db.execute(
            """SELECT event_kind FROM source_revision_events WHERE work_id=?
                 ORDER BY source_revision DESC LIMIT 1""",
            (work_id,),
        ).fetchone()
    if not submission or not latest_revision:
        raise BrainIntakeError("unknown-work")
    try:
        source = json.loads(str(submission["source_private_json"]))
    except json.JSONDecodeError as exc:
        raise BrainIntakeError("source-receipt-corrupt") from exc
    source_key = clean_text(source.get("mediaGroupRef"), 160) or clean_text(source.get("messageRef"), 100)
    expected_work_id, origin_key, run_id = brain_work_identity(
        clean_text(source.get("chatRef"), 100),
        clean_text(source.get("topicRef"), 100),
        source_key,
    )
    if not hmac.compare_digest(expected_work_id, work_id):
        raise BrainConfigurationError("brain-work-identity-mismatch")
    existing_binding = store.lifecycle_binding(work_id)
    if existing_binding:
        if not hmac.compare_digest(str(existing_binding["lifecycle_work_id"]), work_id):
            raise BrainConfigurationError("brain-lifecycle-binding-conflict")
        current = gateway.read_work(work_id)
        if not current:
            raise BrainConfigurationError("brain-lifecycle-receipt-invalid")
        source_revision = int(submission["source_revision"])
        if source_revision > int(current["sourceRevision"]) and current["phase"] != "terminal":
            gateway.update_source_revision(
                work_id,
                source_revision=source_revision,
                expected_sequence=int(current["sequence"]),
                fencing_epoch=int(current["fencingEpoch"]),
                side_effects_started=str(latest_revision["event_kind"]) == "correction-requested",
            )
        return {"ok": True, "bound": True, "duplicate": True}
    receipt = gateway.start_work(
        origin_key=origin_key,
        run_id=run_id,
        intake_agent="josh2",
        current_owner="josh2",
        surface_contract="brain-intake",
        text="",
        has_media=True,
        brain=True,
        generation=1,
        source_revision=int(submission["source_revision"]),
        worker_route="josh2/brain-intake-local-v3",
        classification=(3, "brain-media"),
        work_id=work_id,
    )
    store.bind_lifecycle(work_id, receipt)
    return {"ok": True, "bound": True, "duplicate": receipt.get("phase") != "received"}


def private_envelope(args: argparse.Namespace) -> dict[str, Any]:
    if not args.private_stdin:
        raise BrainAuthorizationError("private-stdin-required")
    try:
        value = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise BrainIntakeError("private-envelope-invalid") from exc
    if not isinstance(value, dict):
        raise BrainIntakeError("private-envelope-shape-invalid")
    return value


def parser() -> argparse.ArgumentParser:
    root_default = Path.home() / ".openclaw" / "private" / "brain-intake"
    repo_default = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.environ.get("BRAIN_INTAKE_ROOT", str(root_default)))
    parser.add_argument("--config", default=os.environ.get("TELEGRAM_INTAKE_LANES", str(repo_default / "config/telegram-intake-lanes.json")))
    parser.add_argument("--rollout", default=os.environ.get("TELEGRAM_LIFECYCLE_ROLLOUT", str(repo_default / "config/telegram-lifecycle-rollout.json")))
    parser.add_argument(
        "--topic-receipt",
        default=os.environ.get("BRAIN_TOPIC_RECEIPT", str(DEFAULT_TOPIC_RECEIPT)),
    )
    parser.add_argument(
        "--authorized-sender-receipt",
        default=os.environ.get(
            "BRAIN_AUTHORIZED_SENDER_RECEIPT", str(DEFAULT_AUTHORIZED_SENDER_RECEIPT),
        ),
    )
    parser.add_argument(
        "--lifecycle-root",
        default=os.environ.get(
            "TELEGRAM_LIFECYCLE_ROOT",
            str(Path.home() / ".openclaw/private/telegram-lifecycle"),
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    configure = sub.add_parser("configure-topic")
    configure.add_argument("--receipt", required=True)
    route = sub.add_parser("route-check")
    route.add_argument("--private-stdin", action="store_true")
    pre = sub.add_parser("predownload")
    pre.add_argument("--private-stdin", action="store_true")
    post = sub.add_parser("postdownload")
    post.add_argument("--private-stdin", action="store_true")
    attachment_failure = sub.add_parser("attachment-failure")
    attachment_failure.add_argument("--private-stdin", action="store_true")
    extract = sub.add_parser("extract")
    extract.add_argument("--work-id", required=True)
    receipt = sub.add_parser("final-receipt")
    receipt.add_argument("--work-id", required=True)
    candidate = sub.add_parser("propose-candidate")
    candidate.add_argument("--private-stdin", action="store_true")
    correction = sub.add_parser("correct")
    correction.add_argument("--private-stdin", action="store_true")
    status = sub.add_parser("status")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--agent", choices=sorted(AUTHORIZED_AGENTS), required=True)
    search.add_argument("--limit", type=int, default=6)
    reference = sub.add_parser("reference-only")
    reference.add_argument("--private-stdin", action="store_true")
    cancel = sub.add_parser("cancel")
    cancel.add_argument("--private-stdin", action="store_true")
    privacy_preview = sub.add_parser("privacy-preview")
    privacy_preview.add_argument("--private-stdin", action="store_true")
    privacy_change = sub.add_parser("privacy-change")
    privacy_change.add_argument("--private-stdin", action="store_true")
    approve = sub.add_parser("approve-candidate")
    approve.add_argument("--private-stdin", action="store_true")
    reject = sub.add_parser("reject-candidate")
    reject.add_argument("--private-stdin", action="store_true")
    supersede = sub.add_parser("supersede-memory")
    supersede.add_argument("--private-stdin", action="store_true")
    preview = sub.add_parser("forget-preview")
    preview.add_argument("--private-stdin", action="store_true")
    forget = sub.add_parser("forget")
    forget.add_argument("--private-stdin", action="store_true")
    hook = sub.add_parser("install-openclaw-hook")
    hook.add_argument("--bundle", required=True)
    hook.add_argument("--backup-root", default=str(Path.home() / ".openclaw/private/openclaw-patches/brain-predownload"))
    hook.add_argument("--rollback-hook", action="store_true")
    return parser


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "configure-topic":
            result = configure_brain_topic(Path(args.config), Path(args.receipt))
        elif args.command == "install-openclaw-hook":
            result = patch_openclaw_ingress(Path(args.bundle), install=not args.rollback_hook, backup_root=Path(args.backup_root))
        elif args.command == "route-check":
            envelope = private_envelope(args)
            config = load_json(Path(args.config))
            group_id, topic_id = resolved_brain_topic(config, Path(args.topic_receipt))
            on_brain_topic = (
                clean_text(envelope.get("chatId"), 80) == group_id
                and clean_text(envelope.get("threadId"), 80) == topic_id
            )
            silent_drop = False
            if on_brain_topic:
                authorized_sender = resolved_authorized_sender(
                    Path(args.authorized_sender_receipt),
                    chat_id=group_id,
                    topic_id=topic_id,
                )
                sender_id = clean_text(envelope.get("senderId"), 120)
                silent_drop = (
                    envelope.get("senderIsBot") is not False
                    or not sender_id
                    or not hmac.compare_digest(authorized_sender, sender_id)
                )
            result = {
                "ok": True,
                "brain": on_brain_topic,
                "handled": silent_drop,
                "silentDrop": silent_drop,
                "owner": "josh2",
                "lane": "brain-intake",
            }
        else:
            store = BrainStore(
                args.root,
                authorized_sender_receipt=Path(args.authorized_sender_receipt),
            )
            if args.command == "predownload":
                envelope = private_envelope(args)
                config = load_json(Path(args.config))
                group_id, topic_id = resolved_brain_topic(config, Path(args.topic_receipt))
                if clean_text(envelope.get("chatId"), 80) != group_id or clean_text(envelope.get("threadId"), 80) != topic_id:
                    result = {"ok": True, "brain": False}
                elif not brain_ingestion_enabled(config, Path(args.rollout)):
                    result = {"ok": False, "brain": True, "errorClass": "brain-intake-disabled"}
                else:
                    media_group = clean_text(envelope.get("mediaGroupId"), 160)
                    source_key = media_group or clean_text(envelope.get("messageId"), 100)
                    expected_work_id, _, _ = brain_work_identity(group_id, topic_id, source_key)
                    existing_binding = store.lifecycle_binding(expected_work_id)
                    with store.connect() as db:
                        existing_source = bool(db.execute(
                            "SELECT 1 FROM submissions WHERE work_id=?", (expected_work_id,),
                        ).fetchone())
                    edited_event = envelope.get("edited") is True
                    if edited_event and not existing_source:
                        result = {
                            "ok": True,
                            "brain": True,
                            "handled": True,
                            "noSource": True,
                            "queued": True,
                            "downloadTokens": [],
                        }
                        existing_binding = None
                    side_effects_started = edited_event and existing_source
                    if existing_binding and not edited_event:
                        from telegram_gateway_lifecycle import GatewayLifecycle, RolloutPolicy
                        gateway = GatewayLifecycle(
                            Path(args.lifecycle_root),
                            rollout=RolloutPolicy.load(Path(args.rollout)),
                            owner="josh2",
                        )
                        lifecycle_receipt = gateway.read_work(expected_work_id)
                        side_effects_started = bool(
                            lifecycle_receipt
                            and (
                                lifecycle_receipt.get("reactionDelivered")
                                or lifecycle_receipt.get("cardCreated")
                                or lifecycle_receipt.get("phase") in {
                                    "acknowledged", "working", "awaiting_input", "verifying", "terminal",
                                }
                            )
                        )
                    if not (edited_event and not existing_source):
                        result = store.begin_submission(
                            envelope,
                            side_effects_started=side_effects_started,
                        )
                        if not edited_event or existing_binding:
                            ensure_brain_lifecycle(
                                store,
                                str(result["workId"]),
                                lifecycle_root=Path(args.lifecycle_root),
                                rollout_path=Path(args.rollout),
                            )
                            result["lifecycleBound"] = True
                            failure_count = 0
                            for capability in result["downloadTokens"]:
                                failure_reason = clean_text(capability.get("failureReason"), 40)
                                if not failure_reason or capability.get("consumed") is True:
                                    continue
                                failure = store.fail_attachment(
                                    work_id=str(result["workId"]),
                                    attachment_id=clean_text(capability.get("attachmentId"), 128),
                                    token=str(capability.get("token") or ""),
                                    reason=failure_reason,
                                )
                                failure_count += 1
                                capability["consumed"] = True
                                capability.pop("token", None)
                                result["phase"] = failure["phase"]
                                result["queued"] = bool(result["queued"] or failure["queued"])
                            result["attachmentFailureCount"] = failure_count
                        else:
                            result["lifecycleBound"] = False
            elif args.command == "postdownload":
                envelope = private_envelope(args)
                result = store.accept_download(
                    work_id=clean_text(envelope.get("workId"), 128),
                    attachment_id=clean_text(envelope.get("attachmentId"), 128),
                    token=str(envelope.get("token") or ""),
                    source_path=Path(str(envelope.get("path") or "")),
                )
            elif args.command == "attachment-failure":
                envelope = private_envelope(args)
                result = store.fail_attachment(
                    work_id=clean_text(envelope.get("workId"), 128),
                    attachment_id=clean_text(envelope.get("attachmentId"), 128),
                    token=str(envelope.get("token") or ""),
                    reason=clean_text(envelope.get("reason"), 40),
                )
            elif args.command == "extract":
                result = store.extract_submission(args.work_id)
            elif args.command == "final-receipt":
                result = store.final_receipt(args.work_id)
                result = {"ok": True, "receipt": result}
            elif args.command == "propose-candidate":
                envelope = private_envelope(args)
                result = store.propose_candidate(
                    work_id=clean_text(envelope.get("workId"), 128),
                    candidate_type=clean_text(envelope.get("candidateType"), 40),
                    subject=str(envelope.get("subject") or ""),
                    predicate=str(envelope.get("predicate") or ""),
                    value=str(envelope.get("value") or ""),
                    privacy=clean_text(envelope.get("privacy"), 40),
                    confidence=envelope.get("confidence") or 0.0,
                )
            elif args.command == "correct":
                envelope = private_envelope(args)
                result = store.correct(
                    clean_text(envelope.get("workId"), 128),
                    subject=str(envelope.get("subject") or ""),
                    predicate=str(envelope.get("predicate") or ""),
                    value=str(envelope.get("value") or ""),
                    authorized_user=clean_text(envelope.get("authorizedUser"), 160),
                    privacy=clean_text(envelope.get("privacy"), 40) or "private",
                )
            elif args.command == "search":
                result = store.search_source(query=args.query, agent=args.agent, limit=args.limit)
            elif args.command == "reference-only":
                envelope = private_envelope(args)
                result = store.mark_reference_only(
                    clean_text(envelope.get("workId"), 128),
                    authorized_user=clean_text(envelope.get("authorizedUser"), 160),
                )
            elif args.command == "cancel":
                envelope = private_envelope(args)
                result = store.cancel_submission(
                    clean_text(envelope.get("workId"), 128),
                    authorized_user=clean_text(envelope.get("authorizedUser"), 160),
                )
            elif args.command == "privacy-preview":
                envelope = private_envelope(args)
                result = store.privacy_change_preview(
                    clean_text(envelope.get("workId"), 128),
                    authorized_user=clean_text(envelope.get("authorizedUser"), 160),
                    privacy=clean_text(envelope.get("privacy"), 40),
                )
            elif args.command == "privacy-change":
                envelope = private_envelope(args)
                result = store.change_privacy(
                    clean_text(envelope.get("workId"), 128),
                    authorized_user=clean_text(envelope.get("authorizedUser"), 160),
                    privacy=clean_text(envelope.get("privacy"), 40),
                    confirmation_token=str(envelope.get("confirmationToken") or ""),
                )
            elif args.command == "approve-candidate":
                envelope = private_envelope(args)
                result = store.approve_candidate(
                    clean_text(envelope.get("workId"), 128),
                    candidate_id=clean_text(envelope.get("candidateId"), 128),
                    authorized_user=clean_text(envelope.get("authorizedUser"), 160),
                )
            elif args.command == "reject-candidate":
                envelope = private_envelope(args)
                result = store.reject_candidate(
                    clean_text(envelope.get("workId"), 128),
                    candidate_id=clean_text(envelope.get("candidateId"), 128),
                    authorized_user=clean_text(envelope.get("authorizedUser"), 160),
                    reason=clean_text(envelope.get("reason"), 40),
                )
            elif args.command == "supersede-memory":
                envelope = private_envelope(args)
                result = store.supersede_memory(
                    clean_text(envelope.get("workId"), 128),
                    candidate_id=clean_text(envelope.get("candidateId"), 128),
                    obsolete_memory_id=clean_text(envelope.get("obsoleteMemoryId"), 128),
                    authorized_user=clean_text(envelope.get("authorizedUser"), 160),
                )
            elif args.command == "forget-preview":
                envelope = private_envelope(args)
                result = store.forget_preview(
                    clean_text(envelope.get("workId"), 128),
                    authorized_user=clean_text(envelope.get("authorizedUser"), 160),
                )
            elif args.command == "forget":
                envelope = private_envelope(args)
                result = store.forget(
                    clean_text(envelope.get("workId"), 128),
                    authorized_user=clean_text(envelope.get("authorizedUser"), 160),
                    confirmation_token=str(envelope.get("confirmationToken") or ""),
                )
            else:
                result = store.status()
    except BrainIntakeError as exc:
        result = {"ok": False, "errorClass": getattr(exc, "code", "brain-intake-error")}
    print(json.dumps(result, indent=2, sort_keys=True))
    durably_isolated = bool(
        args.command == "postdownload"
        and result.get("quarantined") is True
        and result.get("queued") is True
    )
    return 0 if result.get("ok") or durably_isolated else 1


if __name__ == "__main__":
    raise SystemExit(main())
