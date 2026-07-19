import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mission_control_kiosk_watchdog.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("mission_control_kiosk_watchdog", MODULE_PATH)
watchdog = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(watchdog)


def test_runtime_check_uses_repository_qa_python_when_available(monkeypatch, tmp_path):
    qa_python = tmp_path / ".venv-qa" / "bin" / "python"
    qa_python.parent.mkdir(parents=True)
    qa_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(watchdog, "PLAYWRIGHT_PYTHON", tmp_path / "missing-homebrew-python")
    monkeypatch.setattr(watchdog, "QA_PYTHON", qa_python)
    observed = []

    def fake_run(cmd, timeout=90):
        observed.append((cmd, timeout))
        return type("Result", (), {
            "returncode": 0,
            "stdout": json.dumps({"ok": True, "summary": "Rendered browser check passed"}),
            "stderr": "",
        })()

    monkeypatch.setattr(watchdog, "run", fake_run)
    ok, payload, detail = watchdog.runtime_check()
    assert ok is True
    assert payload["ok"] is True
    assert detail == "Rendered browser check passed"
    assert observed[0][0][0] == str(qa_python)


def test_runtime_check_falls_back_to_current_python_without_qa_runtime(monkeypatch, tmp_path):
    missing = tmp_path / "missing-python"
    monkeypatch.setattr(watchdog, "PLAYWRIGHT_PYTHON", missing)
    monkeypatch.setattr(watchdog, "QA_PYTHON", missing)
    observed = []

    def fake_run(cmd, timeout=90):
        observed.append(cmd)
        return type("Result", (), {
            "returncode": 0,
            "stdout": json.dumps({"ok": True, "summary": "Fallback passed"}),
            "stderr": "",
        })()

    monkeypatch.setattr(watchdog, "run", fake_run)
    assert watchdog.runtime_check()[0] is True
    assert observed[0][0] == sys.executable


def test_runtime_check_prefers_playwright_python_over_qa_runtime(monkeypatch, tmp_path):
    playwright_python = tmp_path / "homebrew" / "python3"
    qa_python = tmp_path / "qa" / "python"
    playwright_python.parent.mkdir(parents=True)
    qa_python.parent.mkdir(parents=True)
    playwright_python.write_text("", encoding="utf-8")
    qa_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(watchdog, "PLAYWRIGHT_PYTHON", playwright_python)
    monkeypatch.setattr(watchdog, "QA_PYTHON", qa_python)
    observed = []

    def fake_run(cmd, timeout=90):
        observed.append(cmd)
        return type("Result", (), {
            "returncode": 0,
            "stdout": json.dumps({"ok": True, "summary": "Playwright passed"}),
            "stderr": "",
        })()

    monkeypatch.setattr(watchdog, "run", fake_run)
    assert watchdog.runtime_check()[0] is True
    assert observed[0][0] == str(playwright_python)


def test_latest_screen_check_status_uses_newest_matching_event(tmp_path):
    events_path = tmp_path / "shared-events.json"
    events_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "time": "2026-07-17T04:00:00Z",
                        "agent": "josh2",
                        "tool": "Control Tower screen check",
                        "status": "blocked",
                    },
                    {
                        "time": "2026-07-17T03:00:00Z",
                        "agent": "josh2",
                        "tool": "Control Tower screen check",
                        "status": "done",
                    },
                    {
                        "time": "2026-07-17T05:00:00Z",
                        "agent": "jaimes",
                        "tool": "Control Tower screen check",
                        "status": "done",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assert watchdog.latest_screen_check_status(events_path) == "blocked"


def test_publication_for_result_clears_prior_failure_on_clean_poll():
    assert watchdog.publication_for_result(
        ok=True,
        detail="Rendered checks passed.",
        prior_status="blocked",
        repaired=False,
        foreground_action=False,
    ) == ("done", "Josh 2.0 screen check recovered", "Rendered checks passed.")


def test_publication_for_result_deduplicates_persistent_failure():
    assert watchdog.publication_for_result(
        ok=False,
        detail="Rendered checks failed.",
        prior_status="blocked",
        repaired=False,
        foreground_action=False,
    ) is None


def test_publication_for_result_opens_new_failure_after_healthy_state():
    assert watchdog.publication_for_result(
        ok=False,
        detail="Rendered checks failed.",
        prior_status="done",
        repaired=False,
        foreground_action=False,
    ) == ("blocked", "Josh 2.0 screen check needs attention", "Rendered checks failed.")


def test_publication_for_result_keeps_routine_success_quiet():
    assert watchdog.publication_for_result(
        ok=True,
        detail="Rendered checks passed.",
        prior_status="done",
        repaired=False,
        foreground_action=False,
    ) is None


def test_publication_for_result_keeps_unopened_self_repair_quiet():
    assert watchdog.publication_for_result(
        ok=True,
        detail="Rendered checks passed.",
        prior_status="done",
        repaired=True,
        foreground_action=True,
    ) is None


def test_main_refreshes_dashboard_after_recovery_publication(monkeypatch, tmp_path):
    actions = []

    monkeypatch.setattr(
        watchdog,
        "runtime_check",
        lambda: (True, {"ok": True, "summary": "Rendered checks passed."}, "Rendered checks passed."),
    )
    monkeypatch.setattr(
        watchdog,
        "ensure_foreground",
        lambda repair=False: {"ok": True, "status": "foreground", "reason": "already-foreground"},
    )
    monkeypatch.setattr(watchdog, "latest_screen_check_status", lambda: "blocked")
    monkeypatch.setattr(watchdog, "WATCHDOG_STATE_PATH", tmp_path / "watchdog-state.json")
    monkeypatch.setattr(watchdog, "change_lease_active", lambda: False)

    def fake_run(cmd, timeout=90):
        actions.append(("refresh", cmd, timeout))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def fake_publish(status, title, detail):
        actions.append(("publish", status, title, detail))

    monkeypatch.setattr(watchdog, "run", fake_run)
    monkeypatch.setattr(watchdog, "publish", fake_publish)
    monkeypatch.setattr(sys, "argv", ["mission_control_kiosk_watchdog.py"])

    assert watchdog.main() == 0
    assert [action[0] for action in actions] == ["refresh", "publish", "refresh"]
    assert actions[1][1:3] == ("done", "Josh 2.0 screen check recovered")


def test_change_lease_defers_without_runtime_or_publication(monkeypatch):
    monkeypatch.setattr(watchdog, "change_lease_active", lambda: True)
    monkeypatch.setattr(watchdog, "runtime_check", lambda: (_ for _ in ()).throw(AssertionError("ran")))
    monkeypatch.setattr(watchdog, "publish", lambda *args: (_ for _ in ()).throw(AssertionError("published")))
    monkeypatch.setattr(sys, "argv", ["mission_control_kiosk_watchdog.py"])

    assert watchdog.main() == 0


def test_second_layout_only_failure_never_repairs_kiosk(monkeypatch, tmp_path):
    state = tmp_path / "watchdog-state.json"
    state.write_text(json.dumps({"failureStreak": 1, "incidentOpen": False}), encoding="utf-8")
    repairs = []
    monkeypatch.setattr(watchdog, "WATCHDOG_STATE_PATH", state)
    monkeypatch.setattr(watchdog, "change_lease_active", lambda: False)
    monkeypatch.setattr(
        watchdog,
        "runtime_check",
        lambda: (False, {"ok": False}, "Rendered layout failed."),
    )

    def foreground(repair=False):
        repairs.append(repair)
        return {"ok": True, "status": "foreground", "reason": "already-foreground"}

    monkeypatch.setattr(watchdog, "ensure_foreground", foreground)
    monkeypatch.setattr(
        watchdog,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    monkeypatch.setattr(sys, "argv", ["mission_control_kiosk_watchdog.py", "--repair", "--no-publish"])

    assert watchdog.main() == 1
    assert repairs == [False]
    assert json.loads(state.read_text())["failureStreak"] == 2
