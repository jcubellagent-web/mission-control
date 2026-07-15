from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "ecosystem_qa_benchmark.py"
SPEC = importlib.util.spec_from_file_location("ecosystem_qa_benchmark_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"{}"


def test_http_probe_recovers_one_transient_failure() -> None:
    with patch.object(benchmark.urllib.request, "urlopen", side_effect=[OSError("brief restart"), Response(), Response()]), \
         patch.object(benchmark.time, "sleep"):
        result = benchmark.http_performance(samples=2, attempts_per_sample=2)
    assert result["ok"] is True
    assert result["errors"] == 0
    assert result["retryAttempts"] == 1


def health_payload() -> dict:
    return {
        "status": "attention",
        "agents": [{"ok": True, "stale": False} for _ in range(3)],
        "modelRoutesOk": True,
        "cronAttentionCount": 0,
        "controlTowerAgeMinutes": 1.0,
    }


def test_medium_human_action_does_not_fail_operational_health(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "control-tower-live.json").write_text(
        json.dumps({"actionRequired": [{"priority": "medium", "title": "Human decision"}]}),
        encoding="utf-8",
    )
    with patch.object(benchmark, "ROOT", tmp_path), \
         patch.object(benchmark, "execute", return_value={"ok": False, "stdout": json.dumps(health_payload())}):
        result = benchmark.ecosystem_health_check()
    assert result["ok"] is True
    assert result["nonBlockingActionRequired"] == 1
    assert result["blockingActionRequired"] == 0


def test_high_priority_action_still_fails_operational_health(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "control-tower-live.json").write_text(
        json.dumps({"actionRequired": [{"priority": "high", "title": "Runtime outage"}]}),
        encoding="utf-8",
    )
    with patch.object(benchmark, "ROOT", tmp_path), \
         patch.object(benchmark, "execute", return_value={"ok": False, "stdout": json.dumps(health_payload())}):
        result = benchmark.ecosystem_health_check()
    assert result["ok"] is False
    assert result["blockingActionRequired"] == 1
