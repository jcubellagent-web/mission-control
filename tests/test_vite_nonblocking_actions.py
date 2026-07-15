from __future__ import annotations

import fcntl
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_wallet_refresh_is_async_single_flight_and_never_spawn_sync() -> None:
    source = (ROOT / "vite.config.ts").read_text(encoding="utf-8")

    assert "spawnSync" not in source
    assert 'spawn("python3"' in source
    assert "walletRefreshInFlight" in source
    assert "if (walletRefreshInFlight) return walletRefreshInFlight" in source
    assert "void runWalletRefresh().then" in source
    assert "wallet refresh timed out" in source
    assert "status: timedOut ? 124" in source
    assert "finish({ status: 124" not in source


def test_wallet_refresh_keeps_output_bounded() -> None:
    source = (ROOT / "vite.config.ts").read_text(encoding="utf-8")

    assert "walletRefreshOutputLimit" in source
    assert ".slice(0, walletRefreshOutputLimit)" in source


def test_wallet_refresh_uses_host_wide_single_flight_lock(monkeypatch, tmp_path, capsys) -> None:
    path = ROOT / "scripts" / "refresh_agentic_robinhood_wallet_live.py"
    spec = importlib.util.spec_from_file_location("wallet_refresh_lock_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    lock_path = tmp_path / "wallet-refresh.lock"
    monkeypatch.setattr(module, "REFRESH_LOCK_PATH", lock_path)
    monkeypatch.setattr(module, "refresh_wallet", lambda: (_ for _ in ()).throw(AssertionError("must coalesce")))

    lock_path.touch()
    with lock_path.open("a+", encoding="utf-8") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = module.main()

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "status": "already-running"}
