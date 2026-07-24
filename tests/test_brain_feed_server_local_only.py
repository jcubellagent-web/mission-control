from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "brain_feed_server.py"


def load_server_module():
    spec = importlib.util.spec_from_file_location("brain_feed_server_local_only", SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retired_supabase_flag_cannot_start_network_polling(monkeypatch) -> None:
    monkeypatch.setenv("MISSION_CONTROL_SUPABASE_COMMANDS", "1")
    with mock.patch("urllib.request.urlopen", side_effect=AssertionError("unexpected network call")):
        module = load_server_module()

    assert not hasattr(module, "supabase_command_polling_enabled")
    assert not hasattr(module, "_poll_supabase_commands")
    source = SERVER.read_text()
    assert "MISSION_CONTROL_SUPABASE_COMMANDS" not in source
    assert "agent_comms?agent=eq.phone" not in source
