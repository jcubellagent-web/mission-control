from __future__ import annotations

import base64
import importlib.util
import json
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "agent_mfa_broker.py"
SPEC = importlib.util.spec_from_file_location("agent_mfa_broker", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_rfc_6238_sha1_vector() -> None:
    seed = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    assert MODULE.totp(seed, at=59, digits=8) == "94287082"
    assert MODULE.totp(seed, at=59, digits=6) == "287082"


def test_seed_extraction_supports_uri_and_manual_grouping() -> None:
    assert MODULE.seed_from_material(
        "otpauth://totp/Agent?secret=JBSWY3DPEHPK3PXP&issuer=Example"
    ) == "JBSWY3DPEHPK3PXP"
    assert MODULE.seed_from_material(
        "Manual setup key: JBSW Y3DP EHPK 3PXP"
    ) == "JBSWY3DPEHPK3PXP"


def test_origin_and_path_are_both_required() -> None:
    account = {
        "allowedOrigins": ["https://app.alpaca.markets"],
        "allowedPathPrefixes": ["/brokerage/", "/login"],
    }
    assert MODULE.allowed_location(account, "https://app.alpaca.markets/brokerage/new-account")
    assert not MODULE.allowed_location(account, "https://evil.example/brokerage/new-account")
    assert not MODULE.allowed_location(account, "https://app.alpaca.markets/settings/security")
    assert not MODULE.allowed_location(account, "https://app.alpaca.markets/login-redirect")
    assert not MODULE.allowed_location(account, "https://app.alpaca.markets/brokerage/../live")
    assert not MODULE.allowed_location(account, "https://app.alpaca.markets/brokerage/%2e%2e/live")


def test_disabled_or_unknown_accounts_fail_closed() -> None:
    config = {"accounts": {"disabled": {"enabled": False}}}
    for name in ("disabled", "missing"):
        try:
            MODULE.account_config(config, name)
        except MODULE.BrokerError as exc:
            assert "not allowlisted" in str(exc)
        else:
            raise AssertionError("account unexpectedly allowed")


def test_receipts_never_contain_secret_material(tmp_path: Path) -> None:
    path = tmp_path / "receipts.jsonl"
    MODULE.append_receipt(
        path,
        {
            "ok": False,
            "reason": MODULE.redact(
                "secret=JBSWY3DPEHPK3PXP code=123456 otpauth://totp/x?secret=ABC"
            ),
        },
    )
    text = path.read_text()
    assert "JBSWY3DPEHPK3PXP" not in text
    assert "123456" not in text
    assert "otpauth://totp" not in text


def test_cli_has_no_show_code_or_seed_operation() -> None:
    source = SCRIPT.read_text()
    assert 'choices=("status", "self-test", "enroll", "complete")' in source
    assert "show-code" not in source
    assert "show-seed" not in source
    assert "clipboard" not in source.lower()
    assert 'scope.evaluate(\n                r"""' in source


def test_keychain_ffi_declares_argument_types() -> None:
    source = SCRIPT.read_text()
    assert "SecKeychainFindGenericPassword.argtypes" in source
    assert "SecKeychainAddGenericPassword.argtypes" in source
    assert "SecKeychainItemDelete.argtypes" in source


def test_mutating_operations_use_an_exclusive_lock() -> None:
    source = SCRIPT.read_text()
    assert "fcntl.LOCK_EX | fcntl.LOCK_NB" in source
    assert "with self._exclusive_operation():" in source


def test_private_receipts_refuse_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("unchanged")
    receipt = tmp_path / "receipt.jsonl"
    receipt.symlink_to(target)
    try:
        MODULE.append_receipt(receipt, {"ok": True})
    except OSError:
        pass
    else:
        raise AssertionError("receipt symlink unexpectedly followed")
    assert target.read_text() == "unchanged"


def test_checked_in_alpaca_scope_is_paper_only() -> None:
    config = json.loads((Path(__file__).parents[1] / "config" / "agent-mfa-broker.json").read_text())
    account = config["accounts"]["alpaca-paper"]
    assert account["purpose"] == "paper-trading and market-data access only"
    assert account["allowedOrigins"] == ["https://app.alpaca.markets"]


if __name__ == "__main__":
    test_rfc_6238_sha1_vector()
    test_seed_extraction_supports_uri_and_manual_grouping()
    test_origin_and_path_are_both_required()
    test_disabled_or_unknown_accounts_fail_closed()
    with tempfile.TemporaryDirectory() as directory:
        test_receipts_never_contain_secret_material(Path(directory))
    test_cli_has_no_show_code_or_seed_operation()
    test_keychain_ffi_declares_argument_types()
    test_mutating_operations_use_an_exclusive_lock()
    with tempfile.TemporaryDirectory() as directory:
        test_private_receipts_refuse_symlinks(Path(directory))
    test_checked_in_alpaca_scope_is_paper_only()
    print("agent MFA broker tests: ok")
