from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "agent_wallet_signer.py"
SPEC = importlib.util.spec_from_file_location("agent_wallet_signer", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_status_has_no_request_body(tmp_path: Path) -> None:
    assert MODULE.load_request(None, "status") is None


def test_sign_request_is_compacted_json(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"transaction": {"chainId": 4663}}))
    assert MODULE.load_request(str(request), "sign") == b'{"transaction":{"chainId":4663}}'


def test_shared_client_does_not_expose_broadcast_action() -> None:
    source = SCRIPT.read_text()
    assert 'choices=("status", "canary", "validate", "sign")' in source
    assert '"broadcast"' not in source.split("parser.add_argument", 1)[1].split(")", 1)[0]


def test_remote_route_uses_ssh_without_a_shell(monkeypatch) -> None:
    class MissingGateway:
        def exists(self) -> bool:
            return False

        def __str__(self) -> str:
            return "/private/wallet_signer_gateway.py"

    monkeypatch.setattr(MODULE, "PRIVATE_GATEWAY", MissingGateway())
    cmd = MODULE.command("status", "josh2", 30.0)
    assert cmd[:3] == ["ssh", "jaimes", "/usr/bin/python3"]
    assert "shell=True" not in SCRIPT.read_text()
