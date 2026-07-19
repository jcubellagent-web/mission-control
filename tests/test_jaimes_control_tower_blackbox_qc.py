from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "jaimes_control_tower_blackbox_qc.py"
SPEC = importlib.util.spec_from_file_location("jaimes_control_tower_blackbox_qc", MODULE_PATH)
assert SPEC and SPEC.loader
blackbox = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(blackbox)


def live_payload() -> dict[str, object]:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "lastUpdated": now,
        "sourceUpdatedAt": now,
        "brainFeed": {},
        "todayJobs": [],
        "runtimeLayout": {},
    }


def test_current_live_contract_accepts_today_jobs(tmp_path) -> None:
    output = tmp_path / "jaimes-control-tower-blackbox.json"
    with (
        mock.patch.object(blackbox, "fetch", return_value=(live_payload(), 42.0)),
        mock.patch.object(blackbox, "OUTPUT", output),
    ):
        assert blackbox.main() == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["issues"] == []


def test_missing_today_jobs_is_reported_in_plain_english(tmp_path) -> None:
    payload = live_payload()
    payload.pop("todayJobs")
    output = tmp_path / "jaimes-control-tower-blackbox.json"
    with (
        mock.patch.object(blackbox, "fetch", return_value=(payload, 42.0)),
        mock.patch.object(blackbox, "OUTPUT", output),
    ):
        assert blackbox.main() == 1
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["issues"] == ["live payload missing fields: todayJobs"]
