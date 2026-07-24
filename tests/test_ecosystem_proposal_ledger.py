from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "ecosystem_proposal_ledger.py"
SPEC = importlib.util.spec_from_file_location("ecosystem_proposal_ledger_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def test_current_rows_return_one_latest_event_per_proposal() -> None:
    rows = [
        {"id": "a", "status": "approved", "updatedAt": "2026-07-20T00:00:00Z"},
        {"id": "a", "status": "verifying", "updatedAt": "2026-07-21T00:00:00Z"},
        {"id": "b", "status": "proposed", "updatedAt": "2026-07-19T00:00:00Z"},
    ]

    current = {row["id"]: row for row in subject.current_rows(rows)}

    assert set(current) == {"a", "b"}
    assert current["a"]["status"] == "verifying"


def test_status_update_appends_a_transition_instead_of_rewriting_history(tmp_path: Path) -> None:
    ledger = tmp_path / "ecosystem-proposals.json"
    ledger.write_text(json.dumps({
        "version": 1,
        "proposals": [{
            "id": "proposal-a",
            "title": "Safe refactor",
            "summary": "Prepare a bounded change.",
            "owner": "joshex",
            "status": "approved",
            "risk": "low",
            "area": "Reliability",
            "createdAt": "2026-07-20T00:00:00Z",
            "updatedAt": "2026-07-20T00:00:00Z",
        }],
    }), encoding="utf-8")

    with mock.patch.object(subject, "LEDGER", ledger), mock.patch.object(
        sys, "argv", ["ecosystem_proposal_ledger.py", "--id", "proposal-a", "--status", "verifying"]
    ):
        assert subject.main() == 0

    rows = json.loads(ledger.read_text(encoding="utf-8"))["proposals"]
    assert len(rows) == 2
    assert rows[0]["status"] == "approved"
    assert rows[1]["status"] == "verifying"
    assert rows[1]["previousStatus"] == "approved"
    assert rows[1]["risk"] == "low"
