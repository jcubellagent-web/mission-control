#!/usr/bin/env python3
"""Browser-bound TOTP broker for explicitly allowlisted agent-owned accounts.

The broker intentionally has no operation that returns a TOTP seed or code.
It retrieves the seed from the local login Keychain, validates the active Chrome
origin and path, fills the browser challenge, and emits metadata-only JSON.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import fcntl
import hashlib
import hmac
import json
import os
import posixpath
import re
import stat
import struct
import sys
import time
import uuid
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "agent-mfa-broker.json"
DEFAULT_PRIVATE_ROOT = Path.home() / ".openclaw" / "private" / "mfa-broker"
KEYCHAIN_ACCOUNT = "agent-mfa-broker"
ERR_SEC_DUPLICATE_ITEM = -25299
ERR_SEC_ITEM_NOT_FOUND = -25300
MAX_TEXT_BYTES = 250_000


class BrokerError(RuntimeError):
    """Safe operator-facing broker failure."""


def redact(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)(secret|token|password|otp|code|key)=([^&\s]+)", r"\1=***", text)
    text = re.sub(r"(?i)otpauth://[^\s'\"]+", "otpauth://***", text)
    text = re.sub(r"\b[A-Z2-7]{16,}\b", "***", text)
    text = re.sub(r"\b\d{6,8}\b", "***", text)
    return text[:240]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    payload = json.dumps(value, indent=2, sort_keys=True).encode()
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(temp, flags, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def append_receipt(path: Path, value: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    line = (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags, 0o600)
    try:
        verify_private_file(fd)
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise BrokerError("private broker path is not a directory")
    if info.st_uid != os.getuid():
        raise BrokerError("private broker directory has an unexpected owner")
    os.chmod(path, 0o700)


def verify_private_file(fd: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise BrokerError("private broker file is not owner-controlled")
    os.fchmod(fd, 0o600)


def read_private_json(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags)
    try:
        verify_private_file(fd)
        chunks = []
        while True:
            chunk = os.read(fd, 65_536)
            if not chunk:
                break
            chunks.append(chunk)
        value = json.loads(b"".join(chunks))
        return value if isinstance(value, dict) else {}
    finally:
        os.close(fd)


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if data.get("version") != 1 or not isinstance(data.get("accounts"), dict):
        raise BrokerError("unsupported broker configuration")
    return data


def account_config(config: dict[str, Any], account: str) -> dict[str, Any]:
    value = config.get("accounts", {}).get(account)
    if not isinstance(value, dict) or not value.get("enabled"):
        raise BrokerError("account is not allowlisted")
    if not value.get("keychainService") or not value.get("allowedOrigins"):
        raise BrokerError("allowlisted account is incomplete")
    return value


def allowed_location(account: dict[str, Any], url: str) -> bool:
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in account.get("allowedOrigins", []):
        return False
    decoded_path = unquote(parsed.path)
    if not decoded_path.startswith("/") or "\\" in decoded_path or "\x00" in decoded_path:
        return False
    normalized_path = posixpath.normpath(decoded_path)
    if decoded_path.endswith("/") and normalized_path != "/":
        normalized_path += "/"
    if normalized_path != decoded_path:
        return False
    prefixes = account.get("allowedPathPrefixes", [])
    for prefix in prefixes:
        prefix = str(prefix)
        if prefix.endswith("/"):
            if decoded_path == prefix[:-1] or decoded_path.startswith(prefix):
                return True
        elif decoded_path == prefix or decoded_path.startswith(prefix + "/"):
            return True
    return False


def normalize_seed(value: str) -> str:
    compact = re.sub(r"[\s-]+", "", value or "").upper().rstrip("=")
    if not 16 <= len(compact) <= 256 or re.fullmatch(r"[A-Z2-7]+", compact) is None:
        raise BrokerError("invalid TOTP enrollment material")
    try:
        base64.b32decode(compact + "=" * ((8 - len(compact) % 8) % 8), casefold=False)
    except Exception as exc:
        raise BrokerError("invalid TOTP enrollment material") from exc
    return compact


def seed_from_material(
    material: str,
    *,
    allow_standalone: bool = False,
    standalone_requires_digit: bool = True,
) -> str:
    if len(material.encode(errors="ignore")) > MAX_TEXT_BYTES:
        raise BrokerError("enrollment material is too large")
    candidates = [material]
    for _ in range(2):
        decoded = unquote(candidates[-1])
        if decoded == candidates[-1]:
            break
        candidates.append(decoded)
    for text in candidates:
        match = re.search(r"otpauth://totp/[^\s'\"<>]+", text, re.I)
        if match:
            query = parse_qs(urlsplit(match.group(0)).query)
            secret = (query.get("secret") or [""])[0]
            if secret:
                return normalize_seed(secret)
    contextual = re.compile(
        r"(?i)(?:manual(?:\s+setup)?(?:\s+key)?|setup\s+key|secret|authenticator\s+key)"
        r"\s*[:=-]?\s*((?:[A-Z2-7]{4}[\s-]?){4,64})"
    )
    match = contextual.search(material)
    if match:
        return normalize_seed(match.group(1))
    if allow_standalone:
        for line in material.splitlines():
            raw = line.strip()
            if not raw or re.search(r"[a-z]", raw):
                continue
            compact = re.sub(r"[\s-]+", "", raw).rstrip("=")
            if standalone_requires_digit and re.search(r"[2-7]", compact) is None:
                continue
            try:
                return normalize_seed(compact)
            except BrokerError:
                continue
    raise BrokerError("no supported TOTP enrollment material found")


def totp(seed: str, at: int | None = None, digits: int = 6, period: int = 30) -> str:
    when = int(time.time() if at is None else at)
    padding = "=" * ((8 - len(seed) % 8) % 8)
    key = base64.b32decode(seed + padding, casefold=True)
    counter = struct.pack(">Q", when // period)
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def wait_for_viable_window(period: int = 30, minimum_seconds: int = 10) -> int:
    now = int(time.time())
    remaining = period - (now % period)
    if remaining < minimum_seconds:
        time.sleep(remaining + 1)
        now = int(time.time())
    return now


class KeychainStore:
    """Minimal Security.framework wrapper; values never enter argv or stdout."""

    def __init__(self) -> None:
        self.security = ctypes.CDLL(
            "/System/Library/Frameworks/Security.framework/Security"
        )
        self.core = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        self.security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainFindGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainAddGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.security.SecKeychainItemDelete.restype = ctypes.c_int32
        self.security.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
        self.security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self.security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.core.CFRelease.argtypes = [ctypes.c_void_p]
        self.core.CFRelease.restype = None

    @staticmethod
    def _encoded(value: str) -> bytes:
        return value.encode("utf-8")

    def _find(self, service: str) -> tuple[bytes, ctypes.c_void_p] | None:
        service_bytes = self._encoded(service)
        account_bytes = self._encoded(KEYCHAIN_ACCOUNT)
        length = ctypes.c_uint32()
        data = ctypes.c_void_p()
        item = ctypes.c_void_p()
        status = self.security.SecKeychainFindGenericPassword(
            None,
            len(service_bytes),
            service_bytes,
            len(account_bytes),
            account_bytes,
            ctypes.byref(length),
            ctypes.byref(data),
            ctypes.byref(item),
        )
        if status == ERR_SEC_ITEM_NOT_FOUND:
            return None
        if status != 0:
            raise BrokerError(f"Keychain lookup failed ({status})")
        try:
            secret = ctypes.string_at(data, length.value)
        finally:
            self.security.SecKeychainItemFreeContent(None, data)
        return secret, item

    def has(self, service: str) -> bool:
        found = self._find(service)
        if found is None:
            return False
        _, item = found
        if item:
            self.core.CFRelease(item)
        return True

    def read(self, service: str) -> str:
        found = self._find(service)
        if found is None:
            raise BrokerError("MFA seed is not enrolled")
        secret, item = found
        try:
            return secret.decode("utf-8")
        finally:
            if item:
                self.core.CFRelease(item)

    def add(self, service: str, secret: str) -> None:
        service_bytes = self._encoded(service)
        account_bytes = self._encoded(KEYCHAIN_ACCOUNT)
        secret_bytes = self._encoded(secret)
        item = ctypes.c_void_p()
        status = self.security.SecKeychainAddGenericPassword(
            None,
            len(service_bytes),
            service_bytes,
            len(account_bytes),
            account_bytes,
            len(secret_bytes),
            secret_bytes,
            ctypes.byref(item),
        )
        if item:
            self.core.CFRelease(item)
        if status == ERR_SEC_DUPLICATE_ITEM:
            raise BrokerError("MFA seed already enrolled")
        if status != 0:
            raise BrokerError(f"Keychain enrollment failed ({status})")

    def delete(self, service: str) -> None:
        found = self._find(service)
        if found is None:
            return
        _, item = found
        try:
            status = self.security.SecKeychainItemDelete(item)
            if status != 0:
                raise BrokerError(f"Keychain rollback failed ({status})")
        finally:
            if item:
                self.core.CFRelease(item)


class BrowserSession(AbstractContextManager["BrowserSession"]):
    def __init__(self, account: dict[str, Any], cdp_url: str) -> None:
        self.account = account
        self.cdp_url = cdp_url
        self.playwright = None
        self.browser = None
        self.page = None

    def __enter__(self) -> "BrowserSession":
        from playwright.sync_api import sync_playwright

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.connect_over_cdp(self.cdp_url)
        pages = [page for context in self.browser.contexts for page in context.pages]
        matches = [page for page in pages if allowed_location(self.account, page.url)]
        if len(matches) != 1:
            raise BrokerError("expected exactly one allowlisted browser page")
        self.page = matches[0]
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.playwright:
            self.playwright.stop()

    def location(self) -> tuple[str, str]:
        if self.page is None:
            raise BrokerError("browser session is not connected")
        parsed = urlsplit(self.page.url)
        return f"{parsed.scheme}://{parsed.netloc}", parsed.path

    def _scope(self):
        if self.page is None:
            raise BrokerError("browser session is not connected")
        dialogs = self.page.locator('[role="dialog"]:visible')
        if dialogs.count() > 1:
            raise BrokerError("multiple visible dialogs make MFA scope ambiguous")
        return dialogs.first if dialogs.count() == 1 else self.page.locator("body")

    def extract_enrollment_seed(self) -> str:
        if self.page is None:
            raise BrokerError("browser session is not connected")
        button_name = self.account.get("enrollmentButtonName", "Enter code manually")
        scope = self._scope()
        button = scope.get_by_role("button", name=button_name, exact=True)
        if button.count() and button.first.is_visible():
            button.first.click()
        deadline = time.monotonic() + 5
        while True:
            scope = self._scope()
            material = scope.evaluate(
                r"""root => {
                const chunks = [root?.innerText || ""];
                for (const el of root.querySelectorAll(
                    'input,code,pre,[href*="otpauth"],[src*="otpauth"],[data-otp-secret]'
                )) {
                    if (el.value) chunks.push(String(el.value));
                    if (el.textContent) chunks.push(String(el.textContent));
                    for (const attr of el.attributes || []) chunks.push(String(attr.value));
                }
                return chunks.join("\n").slice(0, 250000);
            }"""
            )
            try:
                return seed_from_material(
                    str(material or ""),
                    allow_standalone=bool(self.account.get("allowStandaloneSeed")),
                    standalone_requires_digit=bool(
                        self.account.get("standaloneSeedRequiresDigit", True)
                    ),
                )
            except BrokerError:
                if time.monotonic() >= deadline:
                    raise
                self.page.wait_for_timeout(250)

    def _visible_code_inputs(self):
        if self.page is None:
            raise BrokerError("browser session is not connected")
        inputs = []
        for locator in self._scope().locator("input:visible").all():
            placeholder = (locator.get_attribute("placeholder") or "").lower()
            input_type = (locator.get_attribute("type") or "text").lower()
            if "search" in placeholder or input_type in {"search", "hidden"}:
                continue
            autocomplete = (locator.get_attribute("autocomplete") or "").lower()
            inputmode = (locator.get_attribute("inputmode") or "").lower()
            maxlength = locator.get_attribute("maxlength") or ""
            if autocomplete == "one-time-code" or inputmode in {"numeric", "decimal"} or maxlength in {"1", "6", "8"}:
                inputs.append(locator)
        return inputs

    def fill_and_submit(self, code: str, advance_setup: bool) -> bool:
        if self.page is None:
            raise BrokerError("browser session is not connected")
        scope = self._scope()
        visible_text = scope.inner_text().lower()
        patterns = self.account.get(
            "challengeTextPatterns",
            ["authenticator", "verification code", "security code", "two-factor", "2fa"],
        )
        if not any(str(pattern).lower() in visible_text for pattern in patterns):
            raise BrokerError("allowlisted MFA challenge marker was not found")
        if advance_setup:
            next_button = scope.get_by_role("button", name="Next", exact=True)
            if next_button.count() and next_button.first.is_visible():
                next_button.first.click()
        deadline = time.monotonic() + 5
        inputs = self._visible_code_inputs()
        while not inputs and time.monotonic() < deadline:
            self.page.wait_for_timeout(250)
            inputs = self._visible_code_inputs()
        if len(inputs) == 1:
            inputs[0].fill(code)
        elif len(inputs) >= len(code) and all((item.get_attribute("maxlength") or "") == "1" for item in inputs[: len(code)]):
            for item, digit in zip(inputs, code):
                item.fill(digit)
        elif len(inputs) == len(code) and all(
            (item.get_attribute("autocomplete") or "").lower() == "one-time-code"
            and (item.get_attribute("inputmode") or "").lower() in {"numeric", "decimal"}
            for item in inputs
        ):
            for item, digit in zip(inputs, code):
                item.fill(digit)
        else:
            raise BrokerError("allowlisted MFA input was not found")
        for name in self.account.get("submitButtonNames", ["Verify", "Next", "Continue"]):
            button = self._scope().get_by_role("button", name=name, exact=True)
            if button.count() and button.first.is_visible() and button.first.is_enabled():
                button.first.click()
                self.page.wait_for_timeout(1500)
                return True
        raise BrokerError("allowlisted MFA submit control was not found")

    def enrollment_finished(self) -> bool:
        if self.page is None:
            return False
        heading = self.account.get("setupHeading", "Set up authenticator app")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            marker = self.page.get_by_text(heading, exact=True)
            error_text = self.page.get_by_text(re.compile(r"invalid|incorrect|expired|try again", re.I))
            visible_error = error_text.count() and error_text.first.is_visible()
            if visible_error:
                return False
            if not (marker.count() and marker.first.is_visible()) and not self._visible_code_inputs():
                return True
            self.page.wait_for_timeout(250)
        return False


class Broker:
    def __init__(self, config_path: Path, private_root: Path = DEFAULT_PRIVATE_ROOT) -> None:
        self.config = load_config(config_path)
        self.private_root = private_root
        self.state_path = private_root / "state.json"
        self.receipt_path = private_root / "receipts.jsonl"
        self.lock_path = private_root / "broker.lock"
        self.keychain = KeychainStore()

    @contextmanager
    def _exclusive_operation(self):
        ensure_private_directory(self.private_root)
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(self.lock_path, flags, 0o600)
        try:
            verify_private_file(fd)
            deadline = time.monotonic() + 10
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise BrokerError("another MFA broker operation is still active")
                    time.sleep(0.1)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _state(self) -> dict[str, Any]:
        try:
            return read_private_json(self.state_path)
        except FileNotFoundError:
            return {}

    def _consume_window(self, account: str, at: int, period: int) -> None:
        state = self._state()
        window = at // period
        record = state.get(account) or {}
        if record.get("lastWindow") == window:
            raise BrokerError("an MFA attempt already used this time window")
        record["lastWindow"] = window
        record["updatedAt"] = now_iso()
        state[account] = record
        atomic_json(self.state_path, state)

    def _receipt(self, account: str, action: str, ok: bool, origin: str = "", path: str = "", reason: str = "") -> str:
        receipt_id = uuid.uuid4().hex
        append_receipt(
            self.receipt_path,
            {
                "id": receipt_id,
                "time": now_iso(),
                "account": account,
                "action": action,
                "ok": bool(ok),
                "origin": origin,
                "path": path,
                "reason": redact(reason),
                "secretExposed": False,
                "codeExposed": False,
            },
        )
        return receipt_id

    def status(self, account_name: str) -> dict[str, Any]:
        account = account_config(self.config, account_name)
        return {
            "ok": True,
            "action": "status",
            "account": account_name,
            "enrolled": self.keychain.has(account["keychainService"]),
            "secretExposed": False,
            "codeExposed": False,
        }

    def enroll(self, account_name: str, approval_ref: str, cdp_url: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,120}", approval_ref or ""):
            raise BrokerError("a bounded enrollment approval reference is required")
        with self._exclusive_operation():
            return self._enroll_locked(account_name, approval_ref, cdp_url)

    def _enroll_locked(self, account_name: str, approval_ref: str, cdp_url: str) -> dict[str, Any]:
        account = account_config(self.config, account_name)
        service = account["keychainService"]
        if self.keychain.has(service):
            raise BrokerError("MFA seed already enrolled")
        origin = path = ""
        stored = False
        try:
            with BrowserSession(account, cdp_url) as browser:
                origin, path = browser.location()
                seed = browser.extract_enrollment_seed()
                at = wait_for_viable_window()
                code = totp(seed, at=at)
                self.keychain.add(service, seed)
                stored = True
                self._consume_window(account_name, at, 30)
                browser.fill_and_submit(code, advance_setup=True)
                if not browser.enrollment_finished():
                    raise BrokerError("MFA enrollment was not accepted")
            receipt_id = self._receipt(account_name, "enroll", True, origin, path)
            return {
                "ok": True,
                "action": "enroll",
                "account": account_name,
                "enrolled": True,
                "browserInjected": True,
                "submitted": True,
                "receiptId": receipt_id,
                "secretExposed": False,
                "codeExposed": False,
            }
        except Exception as exc:
            if stored:
                self.keychain.delete(service)
            self._receipt(account_name, "enroll", False, origin, path, str(exc))
            raise

    def complete(self, account_name: str, cdp_url: str) -> dict[str, Any]:
        with self._exclusive_operation():
            return self._complete_locked(account_name, cdp_url)

    def _complete_locked(self, account_name: str, cdp_url: str) -> dict[str, Any]:
        account = account_config(self.config, account_name)
        origin = path = ""
        try:
            with BrowserSession(account, cdp_url) as browser:
                origin, path = browser.location()
                seed = normalize_seed(self.keychain.read(account["keychainService"]))
                at = wait_for_viable_window()
                self._consume_window(account_name, at, 30)
                browser.fill_and_submit(totp(seed, at=at), advance_setup=False)
            receipt_id = self._receipt(account_name, "complete", True, origin, path)
            return {
                "ok": True,
                "action": "complete",
                "account": account_name,
                "browserInjected": True,
                "submitted": True,
                "receiptId": receipt_id,
                "secretExposed": False,
                "codeExposed": False,
            }
        except Exception as exc:
            self._receipt(account_name, "complete", False, origin, path, str(exc))
            raise


def self_test() -> dict[str, Any]:
    seed = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    if totp(seed, at=59, digits=8) != "94287082":
        raise BrokerError("RFC 6238 vector failed")
    if totp(seed, at=59, digits=6) != "287082":
        raise BrokerError("six-digit TOTP vector failed")
    return {"ok": True, "action": "self-test", "vectors": 2, "secretExposed": False, "codeExposed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Browser-bound MFA for allowlisted agent-owned accounts")
    parser.add_argument("action", choices=("status", "self-test", "enroll", "complete"))
    parser.add_argument("--account")
    parser.add_argument("--approval-ref")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9225")
    args = parser.parse_args()
    try:
        if args.action == "self-test":
            result = self_test()
        else:
            if not args.account:
                raise BrokerError("--account is required")
            broker = Broker(args.config)
            if args.action == "status":
                result = broker.status(args.account)
            elif args.action == "enroll":
                result = broker.enroll(args.account, args.approval_ref or "", args.cdp_url)
            else:
                result = broker.complete(args.account, args.cdp_url)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "action": args.action,
                    "account": args.account or "",
                    "error": type(exc).__name__,
                    "message": redact(exc),
                    "secretExposed": False,
                    "codeExposed": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


#JAIMES: allowlisted routine MFA is injected into the verified browser; agents never receive the seed or code.
