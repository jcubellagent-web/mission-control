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
