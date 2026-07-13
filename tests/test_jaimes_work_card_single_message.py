#!/usr/bin/env python3
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "jaimes_work_card.py"
    spec = importlib.util.spec_from_file_location("jaimes_work_card_single_message", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


card = load_module()


def test_ack_message_is_adopted_instead_of_sending_duplicate():
    args = SimpleNamespace(
        key="single-card", title="Fix duplicate cards", model="model", route="route",
        now="Working", done="Received task", next="Verify", blocker="None", eta="",
        ack_message_id="100", chat_id="-1003589561528", thread_id="17",
        buttons=None, buttons_file=None, routing_buttons=False,
        approval_buttons=False, no_buttons=True, final_summary=False,
        no_final_summary=True, timeout=15, dry_run=False, no_brain_feed=False,
    )
    saved = {}
    with patch.object(card, "load_state", return_value={"cards": {}}), \
         patch.object(card, "save_state", side_effect=lambda state: saved.update(state)), \
         patch.object(card, "edit_card", return_value={"ok": True}) as edit, \
         patch.object(card, "send_card") as send, \
         patch.object(card, "edit_objective_message", return_value={"ok": True}), \
         patch.object(card, "publish_brain_feed"):
        assert card.upsert_card(args, "running") == 0
    edit.assert_called_once()
    assert edit.call_args.args[0] == "100"
    send.assert_not_called()
    assert saved["cards"]["single-card"]["message_id"] == "100"
