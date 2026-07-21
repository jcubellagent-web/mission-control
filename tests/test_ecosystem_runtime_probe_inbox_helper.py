from __future__ import annotations

import importlib.util
from pathlib import Path
import datetime as dt


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ecosystem_runtime_probe.py"
SPEC = importlib.util.spec_from_file_location("ecosystem_runtime_probe_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_configured_inbox_helper_reads_nested_plugin_config() -> None:
    config = {
        "plugins": {
            "entries": {
                "inbox-coordinator": {
                    "enabled": True,
                    "config": {"helperPath": "/canonical/helper.py"},
                }
            }
        }
    }

    assert probe.configured_inbox_helper(config) == "/canonical/helper.py"
    assert probe.inbox_plugin_enabled(config) is True


def test_inbox_plugin_enabled_requires_explicit_boolean_true() -> None:
    def config(enabled=...):
        entry = {} if enabled is ... else {"enabled": enabled}
        return {"plugins": {"entries": {"inbox-coordinator": entry}}}

    assert probe.inbox_plugin_enabled(config(True)) is True
    assert probe.inbox_plugin_enabled(config(False)) is False
    assert probe.inbox_plugin_enabled(config("true")) is False
    assert probe.inbox_plugin_enabled(config(1)) is False
    assert probe.inbox_plugin_enabled(config()) is False


def test_plugin_default_contract_requires_canonical_workspace_segments() -> None:
    canonical = 'path.join(home, ".openclaw", "workspace", "mission-control", "scripts", "josh_telegram_fast_ack.py")'
    legacy = 'path.join(home, ".openclaw", "workspace", "josh_telegram_fast_ack.py")'

    assert probe.plugin_uses_canonical_helper_default(canonical) is True
    assert probe.plugin_uses_canonical_helper_default(legacy) is False


def test_launchd_contract_accepts_direct_helper_or_josh_owned_wrapper(monkeypatch, tmp_path) -> None:
    helper = tmp_path / "josh_telegram_fast_ack.py"
    launcher = tmp_path / "jaimes_telegram_fast_ack_launcher.py"
    monkeypatch.setattr(probe, "CANONICAL_FAST_ACK_SCRIPT", helper)
    monkeypatch.setattr(probe, "CANONICAL_FAST_ACK_LAUNCHER", launcher)

    assert probe.launchd_uses_canonical_inbox_helper(f"arguments = {{ {helper} }}") is True
    assert probe.launchd_uses_canonical_inbox_helper(
        f"arguments = {{ {launcher} }}\nenvironment = {{ TELEGRAM_FAST_ACK_OWNER => josh2 }}"
    ) is True
    assert probe.launchd_uses_canonical_inbox_helper(
        f"arguments = {{ {launcher} }}\nenvironment = {{ TELEGRAM_FAST_ACK_OWNER => jaimes }}"
    ) is False


def test_collect_rejects_disabled_plugin_even_when_helper_paths_match(monkeypatch, tmp_path) -> None:
    helper = tmp_path / "scripts" / "josh_telegram_fast_ack.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("# synthetic helper\n", encoding="utf-8")
    plugin = tmp_path / "plugins" / "inbox-coordinator" / "index.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(
        'path.join(home, "mission-control", "scripts", "josh_telegram_fast_ack.py")',
        encoding="utf-8",
    )
    config = {
        "plugins": {
            "entries": {
                "inbox-coordinator": {
                    "enabled": False,
                    "config": {"helperPath": str(helper)},
                }
            }
        }
    }
    monkeypatch.setattr(probe, "CANONICAL_FAST_ACK_SCRIPT", helper)
    monkeypatch.setattr(probe, "CANONICAL_WORK_CARD_SCRIPT", helper)
    monkeypatch.setattr(probe, "INBOX_PLUGIN_SOURCE", plugin)
    monkeypatch.setattr(probe, "JOSH_WORK_CARD_SCRIPT", helper)
    monkeypatch.setattr(probe, "JOSH_SEND_REPLY_SCRIPT", helper)
    monkeypatch.setattr(probe, "read_json", lambda *args: config)
    monkeypatch.setattr(
        probe,
        "http_json",
        lambda *args: (True, 1.0, {"sourceUpdatedAt": probe.iso()}, "ok"),
    )
    monkeypatch.setattr(probe, "tcp_probe", lambda *args: (True, 1.0, "listening"))
    monkeypatch.setattr(
        probe,
        "launchd_snapshot",
        lambda *args: (True, f"state = running\npid = 123\narguments = {{ {helper} }}"),
    )

    result = probe.collect("http://unused")

    assert result["checks"]["telegramInboxClaimHelper"]["ok"] is False


def test_collect_accepts_enabled_plugin_with_canonical_source_default(monkeypatch, tmp_path) -> None:
    helper = tmp_path / "scripts" / "josh_telegram_fast_ack.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("# synthetic helper\n", encoding="utf-8")
    plugin = tmp_path / "plugins" / "inbox-coordinator" / "index.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(
        'path.join(home, "mission-control", "scripts", "josh_telegram_fast_ack.py")',
        encoding="utf-8",
    )
    config = {
        "plugins": {
            "entries": {"inbox-coordinator": {"enabled": True}}
        }
    }
    monkeypatch.setattr(probe, "CANONICAL_FAST_ACK_SCRIPT", helper)
    monkeypatch.setattr(probe, "CANONICAL_WORK_CARD_SCRIPT", helper)
    monkeypatch.setattr(probe, "INBOX_PLUGIN_SOURCE", plugin)
    monkeypatch.setattr(probe, "JOSH_WORK_CARD_SCRIPT", helper)
    monkeypatch.setattr(probe, "JOSH_SEND_REPLY_SCRIPT", helper)
    monkeypatch.setattr(probe, "read_json", lambda *args: config)
    monkeypatch.setattr(
        probe,
        "http_json",
        lambda *args: (True, 1.0, {"sourceUpdatedAt": probe.iso()}, "ok"),
    )
    monkeypatch.setattr(probe, "tcp_probe", lambda *args: (True, 1.0, "listening"))
    monkeypatch.setattr(
        probe,
        "launchd_snapshot",
        lambda *args: (True, f"state = running\npid = 123\narguments = {{ {helper} }}"),
    )

    result = probe.collect("http://unused")

    assert result["checks"]["telegramInboxClaimHelper"]["ok"] is True


def test_collect_rejects_drifted_runtime_work_card(monkeypatch, tmp_path) -> None:
    runtime = tmp_path / "runtime" / "josh_work_card.py"
    canonical = tmp_path / "canonical" / "josh_work_card.py"
    reply = runtime.with_name("send_josh_reply.py")
    helper = tmp_path / "canonical" / "josh_telegram_fast_ack.py"
    plugin = tmp_path / "plugins" / "inbox-coordinator" / "index.js"
    for path, content in (
        (runtime, "# stale runtime\n"),
        (canonical, "# canonical source\n"),
        (reply, "# transport\n"),
        (helper, "# helper\n"),
        (plugin, 'path.join(home, "mission-control", "scripts", "josh_telegram_fast_ack.py")'),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    config = {"plugins": {"entries": {"inbox-coordinator": {"enabled": True}}}}
    monkeypatch.setattr(probe, "JOSH_WORK_CARD_SCRIPT", runtime)
    monkeypatch.setattr(probe, "JOSH_SEND_REPLY_SCRIPT", reply)
    monkeypatch.setattr(probe, "CANONICAL_WORK_CARD_SCRIPT", canonical)
    monkeypatch.setattr(probe, "CANONICAL_FAST_ACK_SCRIPT", helper)
    monkeypatch.setattr(probe, "INBOX_PLUGIN_SOURCE", plugin)
    monkeypatch.setattr(probe, "read_json", lambda *args: config)
    monkeypatch.setattr(probe, "http_json", lambda *args: (True, 1.0, {"sourceUpdatedAt": probe.iso()}, "ok"))
    monkeypatch.setattr(probe, "tcp_probe", lambda *args: (True, 1.0, "listening"))
    monkeypatch.setattr(probe, "launchd_snapshot", lambda *args: (True, f"arguments = {{ {helper} }}"))

    result = probe.collect("http://unused")

    assert result["checks"]["telegramWorkCardHelper"] == {
        "ok": False,
        "detail": "runtime work-card helper differs from canonical source",
    }


def test_service_failure_streaks_do_not_inherit_unrelated_global_failure() -> None:
    previous = {
        "failureStreak": 9,
        "serviceFailureStreaks": {"brainFeed": 1},
    }
    checks = {
        "controlTower": {"ok": False},
        "brainFeed": {"ok": True},
        "gateway": {"ok": True},
        "telegramFastAck": {"ok": True},
    }

    streaks = probe.next_service_failure_streaks(previous, checks)

    assert streaks["controlTower"] == 1
    assert streaks["brainFeed"] == 0


def test_control_tower_requires_two_consecutive_own_failures_before_restart() -> None:
    now = dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc)
    checks = {
        "controlTower": {"ok": False},
        "brainFeed": {"ok": True},
        "gateway": {"ok": True},
        "telegramFastAck": {"ok": True},
        "sourceFreshness": {"ok": False},
    }

    assert probe.recoverable_services({}, checks, {"controlTower": 1}, now) == []
    assert probe.recoverable_services({}, checks, {"controlTower": 2}, now) == ["controlTower"]


def test_source_freshness_never_becomes_a_restart_target() -> None:
    now = dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc)
    checks = {
        "controlTower": {"ok": True},
        "brainFeed": {"ok": True},
        "gateway": {"ok": True},
        "telegramFastAck": {"ok": True},
        "sourceFreshness": {"ok": False},
    }

    assert probe.recoverable_services({}, checks, {"sourceFreshness": 99}, now) == []


def test_recovery_cooldown_state_requires_two_later_clean_probes_to_clear() -> None:
    recovery = {
        "controlTower": {
            "lastAttemptAt": "2026-07-15T12:00:00Z",
            "attemptsSinceHealthy": 1,
        }
    }
    checks = {service: {"ok": True} for service in probe.SERVICE_LABELS}

    first = probe.next_service_healthy_streaks({}, checks)
    assert first["controlTower"] == 1
    assert probe.clear_stably_healthy_recoveries(recovery, checks, first) == recovery

    second = probe.next_service_healthy_streaks({"serviceHealthyStreaks": first}, checks)
    assert second["controlTower"] == 2
    assert probe.clear_stably_healthy_recoveries(recovery, checks, second) == {}


def test_probe_exit_code_separates_service_outage_from_contract_drift() -> None:
    healthy = {name: {"ok": True} for name in probe.SERVICE_LABELS}
    healthy["sourceFreshness"] = {"ok": True}
    assert probe.probe_exit_code(healthy) == 0

    contract_drift = {name: dict(row) for name, row in healthy.items()}
    contract_drift["sourceFreshness"] = {"ok": False}
    assert probe.probe_exit_code(contract_drift) == 1

    service_down = {name: dict(row) for name, row in healthy.items()}
    service_down["gateway"] = {"ok": False}
    assert probe.probe_exit_code(service_down) == 2
